"""Прикладные службы координируют один репозиторий за транзакцию."""

from __future__ import annotations

from .catalog import CatalogItem, CatalogItemId, CatalogPricingService
from .contracts import OrderShipmentContract, PublishedOfferContract

from .ports import OrderShipmentReader, PublishedOfferReader, UnitOfWorkFactory
from .inventory import ReservationId, StockItem, StockItemId, WarehouseAllocationService
from .kernel import BuyerId, DomainRuleViolation, Money, OrderReference, Quantity, SellerId, Sku, WarehouseId
from .ordering import CatalogOfferSnapshot, Order, OrderId, OrderLineId
from .payment import Payment, PaymentId
from .shipping import Shipment, ShipmentId, ShipmentPlanningService


def _require(value):
    """Возвращает обязательный результат репозитория или сообщает об отсутствии."""
    if value is None:
        raise KeyError("Агрегат не найден.")
    return value


class CatalogApplicationService:
    """Выполняет команды контекста Catalog через его репозиторий."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Сохраняет фабрику инфраструктурных сессий."""
        self._unit_of_work_factory = unit_of_work_factory

    def create_item(self, item_id: str, sku: str, title: str, seller_id: str, amount: str, currency: str) -> dict:
        """Создаёт предложение и сохраняет его актуальное состояние."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            item = CatalogItem.create(CatalogItemId(item_id), Sku(sku), title, SellerId(seller_id), Money(amount, currency))
            uow.repositories().catalog().save(item)
            return item.to_state()

    def published_offer(self, item_id: str) -> PublishedOfferContract:
        """Возвращает скалярный контракт предложения для другого контекста."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            item, _ = _require(uow.repositories().catalog().get(item_id))
            quote = CatalogPricingService().quote([item])[0].to_dict()
            return PublishedOfferContract(quote["sku"], quote["title"], quote["seller_id"], quote["amount"], quote["currency"])


class OrderingApplicationService:
    """Создаёт заказ из каталожного ценового снимка, не импортируя HTTP.

    Note:
        Стадия развивает минимальный ``Order``: вместо передачи цены из
        входного адаптера служба получает опубликованный скалярный контракт
        Catalog и строит локальный снимок. Гарантия цены строки и команды
        одного корня сохраняется.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, published_offer_reader: PublishedOfferReader) -> None:
        """Сохраняет транзакции и явную границу чтения Catalog.

        Args:
            unit_of_work_factory: Фабрика локальных операций Ordering.
            published_offer_reader: Порт опубликованных скалярных предложений.
        """
        self._unit_of_work_factory = unit_of_work_factory
        self._published_offer_reader = published_offer_reader

    def create_order(self, order_id: str, buyer_id: str, currency: str) -> dict:
        """Создаёт пустой корень заказа."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            order = Order.create_draft(OrderId(order_id), BuyerId(buyer_id), currency)
            uow.repositories().ordering().save(order)
            return order.to_state()

    def add_catalog_line(self, order_id: str, line_id: str, catalog_item_id: str, quantity: int) -> dict:
        """Получает ценовой снимок и передаёт его корню заказа.

        Note:
            Эта стадия заменяет прямые параметры цены и названия из минимальной
            модели ссылкой на опубликованное предложение. Гарантия сохраняется:
            корень получает неизменяемый снимок цены строки.
        """
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories()
            order, version = _require(repo.ordering().get(order_id))
            published_offer = self._published_offer_reader.published_offer(catalog_item_id)
            offer = CatalogOfferSnapshot(
                Sku(published_offer.sku),
                published_offer.title,
                SellerId(published_offer.seller_id),
                Money(published_offer.amount, published_offer.currency),
            )
            order.add_line(OrderLineId(line_id), offer, Quantity(quantity))
            repo.ordering().save(order, version)
            return order.to_state()

    def shipment_contract(self, order_id: str) -> OrderShipmentContract:
        """Переводит состояние заказа в минимальный скалярный контракт Shipping."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            order, _ = _require(uow.repositories().ordering().get(order_id))
            state = order.to_state()
            if state["status"] != "PLACED":
                raise DomainRuleViolation("Создать отправление можно только для размещённого заказа.")
            return OrderShipmentContract(state["id"], state["address"], state["lines"])

    def place(self, order_id: str, address: str) -> dict:
        """Фиксирует адрес и размещает заказ в одной локальной транзакции."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().ordering()
            order, version = _require(repo.get(order_id))
            order.set_shipping_address(address)
            order.place()
            repo.save(order, version)
            return order.to_state()

class InventoryApplicationService:
    """Применяет план распределения к одному складскому агрегату."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Сохраняет инфраструктуру и доменную службу выбора позиции."""
        self._unit_of_work_factory, self._allocation = unit_of_work_factory, WarehouseAllocationService()

    def create_stock_item(self, item_id: str, warehouse_id: str, sku: str, quantity: int) -> dict:
        """Создаёт позицию и принимает начальный приход."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            item = StockItem.create(StockItemId(item_id), WarehouseId(warehouse_id), Sku(sku))
            item.receive(Quantity(quantity))
            uow.repositories().inventory().save(item)
            return item.to_state()

    def reserve(self, stock_item_id: str, reservation_id: str, order_id: str, quantity: int) -> dict:
        """Резервирует товар в одном выбранном агрегате."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().inventory()
            item, version = _require(repo.get(stock_item_id))
            plan = self._allocation.allocate([item], Quantity(quantity))
            item.reserve(ReservationId(reservation_id), OrderReference(order_id), Quantity(plan.quantity))
            repo.save(item, version)
            return item.to_state()

    def reserve_by_sku(self, reservation_id: str, order_id: str, sku: str, quantity: int) -> dict:
        """Выбирает позицию среди нескольких корней и применяет готовый план."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories()
            candidates = repo.inventory().find_by_sku(Sku(sku).value())
            plan = self._allocation.allocate([item for item, _ in candidates], Quantity(quantity))
            selected, version = next((item, item_version) for item, item_version in candidates if item.id().value() == plan.stock_item_id)
            selected.reserve(ReservationId(reservation_id), OrderReference(order_id), Quantity(plan.quantity))
            repo.inventory().save(selected, version)
            return selected.to_state()


class PaymentApplicationService:
    """Применяет команды одного платежного корня."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Сохраняет фабрику сессий."""
        self._unit_of_work_factory = unit_of_work_factory

    def authorize(self, payment_id: str, order_id: str, amount: str, currency: str) -> dict:
        """Создаёт и авторизует демонстрационный платёж."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            payment = Payment.request_authorization(PaymentId(payment_id), OrderReference(order_id), Money(amount, currency))
            payment.authorize()
            uow.repositories().payment().save(payment)
            return payment.to_state()

    def capture(self, payment_id: str) -> dict:
        """Захватывает существующую авторизацию через один платёжный корень."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().payment()
            payment, version = _require(repo.get(payment_id))
            payment.capture()
            repo.save(payment, version)
            return payment.to_state()


class ShippingApplicationService:
    """Создаёт отправление из снимка заказа и передаёт план диспетчеризации."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, order_shipment_reader: OrderShipmentReader) -> None:
        """Сохраняет транзакции, планировщик и границу чтения Ordering."""
        self._unit_of_work_factory, self._planning = unit_of_work_factory, ShipmentPlanningService()
        self._order_shipment_reader = order_shipment_reader

    def create(self, shipment_id: str, order_state: dict) -> dict:
        """Создаёт отправление из скалярного снимка, а не из объекта Order."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            shipment = Shipment.create(ShipmentId(shipment_id), OrderReference(order_state["id"]), order_state["address"], order_state["lines"])
            uow.repositories().shipping().save(shipment)
            return shipment.to_state()

    def create_for_order(self, shipment_id: str, order_id: str) -> dict:
        """Создаёт отправление из скалярного контракта Ordering."""
        contract = self._order_shipment_reader.shipment_contract(order_id)
        return self.create(shipment_id, {"id": contract.order_id, "address": contract.address, "lines": contract.lines})

    def dispatch_ready(self, shipment_ids: list[str]) -> list[str]:
        """Строит план и передаёт каждое отправление отдельной командой.

        План может читать несколько корней, но его применение не образует
        общую транзакционную границу: для каждого идентификатора открывается
        собственная Unit of Work и изменяется только один ``Shipment``.
        """
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().shipping()
            roots = [_require(repo.get(item)) for item in shipment_ids]
            plan = self._planning.plan([root for root, _ in roots])
        dispatched: list[str] = []
        for shipment_id in plan.shipment_ids:
            with self._unit_of_work_factory.unit_of_work() as uow:
                repo = uow.repositories().shipping()
                shipment, version = _require(repo.get(shipment_id))
                shipment.dispatch()
                repo.save(shipment, version)
                dispatched.append(shipment_id)
        return dispatched
