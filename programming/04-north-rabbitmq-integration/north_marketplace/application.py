"""Прикладные службы контекстов и их локальные транзакции."""

from __future__ import annotations

from .catalog import CatalogItem, CatalogItemId, CatalogPricingService
from .contracts import OrderShipmentContract, PublishedOfferContract
from .ports import OrderShipmentReader, PublishedOfferReader, UnitOfWorkFactory
from .inventory import ReservationId, StockItem, StockItemId, WarehouseAllocationService
from .kernel import BuyerId, DomainRuleViolation, Money, OrderLineReference, OrderReference, Quantity, SellerId, Sku, WarehouseId
from .ordering import CatalogOfferSnapshot, Order, OrderId, OrderLineId
from .payment import Payment, PaymentId
from .shipping import Shipment, ShipmentId, ShipmentPlanningService


def _require(value):
    """Возвращает обязательный результат репозитория или сообщает об отсутствии."""
    if value is None:
        raise KeyError("Агрегат не найден.")
    return value


class CatalogApplicationService:
    """Выполняет локальные команды контекста Catalog."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Сохраняет фабрику Unit of Work."""
        self._unit_of_work_factory = unit_of_work_factory

    def create_item(self, item_id: str, sku: str, title: str, seller_id: str, amount: str, currency: str) -> dict:
        """Создаёт предложение и фиксирует его актуальное состояние."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            item = CatalogItem.create(CatalogItemId(item_id), Sku(sku), title, SellerId(seller_id), Money(amount, currency))
            uow.repositories().catalog().save(item)
            return item.to_state()

    def published_offer(self, item_id: str) -> PublishedOfferContract:
        """Возвращает скалярный контракт опубликованного предложения Catalog."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            item, _ = _require(uow.repositories().catalog().get(item_id))
            quote = CatalogPricingService().quote([item])[0].to_dict()
            return PublishedOfferContract(quote["sku"], quote["title"], quote["seller_id"], quote["amount"], quote["currency"])


class OrderingApplicationService:
    """Переводит HTTP/CLI-команды в вызовы корня ``Order``."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, published_offer_reader: PublishedOfferReader | None = None) -> None:
        """Сохраняет хранилище и явную границу чтения Catalog."""
        self._unit_of_work_factory = unit_of_work_factory
        self._published_offer_reader = published_offer_reader

    def create_order(self, order_id: str, buyer_id: str, currency: str) -> dict:
        """Создаёт черновик заказа."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            order = Order.create_draft(OrderId(order_id), BuyerId(buyer_id), currency)
            uow.repositories().ordering().save(order)
            return order.to_state()

    def add_catalog_line(self, order_id: str, line_id: str, catalog_item_id: str, quantity: int) -> dict:
        """Переводит опубликованный скалярный контракт Catalog в модель Ordering.

        Note:
            Контракт и снимок строки сохранены от стадии 03. RabbitMQ меняет
            только доставку производного уведомления после commit, а не
            предметную команду заказа.
        """
        with self._unit_of_work_factory.unit_of_work() as uow:
            repos = uow.repositories()
            order, version = _require(repos.ordering().get(order_id))
            if self._published_offer_reader is None:
                raise RuntimeError("Для добавления строки нужен читатель опубликованных предложений.")
            published = self._published_offer_reader.published_offer(catalog_item_id)
            snapshot = CatalogOfferSnapshot(Sku(published.sku), published.title, SellerId(published.seller_id), Money(published.amount, published.currency))
            order.add_line(OrderLineId(line_id), snapshot, Quantity(quantity))
            repos.ordering().save(order, version)
            return order.to_state()

    def shipment_contract(self, order_id: str) -> OrderShipmentContract:
        """Возвращает скалярный контракт размещённого заказа для Shipping."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            order, _ = _require(uow.repositories().ordering().get(order_id))
            state = order.to_state()
            if state["status"] not in {"PLACED", "FULFILLED"}:
                raise DomainRuleViolation("Создать отправление можно только для размещённого заказа.")
            return OrderShipmentContract(state["id"], state["address"], state["lines"])

    def place(self, order_id: str, address: str) -> dict:
        """Размещает заказ и сохраняет ``OrderPlaced`` в той же транзакции."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().ordering()
            order, version = _require(repo.get(order_id))
            order.set_shipping_address(address)
            order.place()
            uow.save_aggregate("Ordering", "Order", repo, order, version, order_id)
            return order.to_state()

    def fulfill(self, order_id: str, causation_id: str) -> dict:
        """Фиксирует итог успеха после захвата платежа."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().ordering()
            order, version = _require(repo.get(order_id))
            if order.to_state()["status"] == "FULFILLED":
                return order.to_state()
            order.fulfill()
            uow.save_aggregate("Ordering", "Order", repo, order, version, order_id, causation_id)
            return order.to_state()

    def reject_fulfillment(self, order_id: str, reason: str, causation_id: str) -> dict:
        """Фиксирует итоговый отказ после предметной компенсации."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().ordering()
            order, version = _require(repo.get(order_id))
            if order.to_state()["status"] == "FULFILLMENT_REJECTED":
                return order.to_state()
            order.reject_fulfillment(reason)
            uow.save_aggregate("Ordering", "Order", repo, order, version, order_id, causation_id)
            return order.to_state()

    def get(self, order_id: str) -> dict:
        """Возвращает текущее состояние заказа."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            return _require(uow.repositories().ordering().get(order_id))[0].to_state()


class InventoryApplicationService:
    """Выполняет локальные команды Inventory и публикует факты корня."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Сохраняет базу и чистую службу выбора позиции."""
        self._unit_of_work_factory, self._allocation = unit_of_work_factory, WarehouseAllocationService()

    def create_stock_item(self, item_id: str, warehouse_id: str, sku: str, quantity: int) -> dict:
        """Создаёт складскую позицию с начальным приходом."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            item = StockItem.create(StockItemId(item_id), WarehouseId(warehouse_id), Sku(sku))
            item.receive(Quantity(quantity))
            uow.repositories().inventory().save(item)
            return item.to_state()

    def reserve_for_line(self, order_id: str, line_id: str, sku: str, reservation_id: str, quantity: int, causation_id: str) -> dict:
        """Выбирает один складской корень и выполняет реальную команду резерва."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            candidates = uow.repositories().inventory().find_by_sku(Sku(sku).value())
            if not candidates:
                raise KeyError("Складская позиция для SKU не найдена.")
            try:
                plan = self._allocation.allocate([item for item, _ in candidates], Quantity(quantity))
                selected, version = next((item, item_version) for item, item_version in candidates if item.id().value() == plan.stock_item_id)
                requested = Quantity(plan.quantity)
            except DomainRuleViolation:
                # При отсутствии плана корень всё равно фиксирует предметный
                # факт отказа резерва; выбор первой позиции детерминирован.
                selected, version = candidates[0]
                requested = Quantity(quantity)
            existing = next((item for item in selected.to_state()["reservations"] if item["id"] == reservation_id), None)
            if existing is None:
                selected.reserve(ReservationId(reservation_id), OrderReference(order_id), OrderLineReference(line_id), requested)
                uow.save_aggregate("Inventory", "StockItem", uow.repositories().inventory(), selected, version, order_id, causation_id)
            return selected.to_state()

    def release_reservation(self, stock_item_id: str, reservation_id: str, order_id: str, causation_id: str) -> dict:
        """Освобождает реальный подтверждённый резерв идемпотентно."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().inventory()
            item, version = _require(repo.get(stock_item_id))
            state = item.to_state()
            reservation = next((entry for entry in state["reservations"] if entry["id"] == reservation_id), None)
            if reservation is not None and reservation["active"]:
                item.release(ReservationId(reservation_id))
                uow.save_aggregate("Inventory", "StockItem", repo, item, version, order_id, causation_id)
            return item.to_state()

    def reserve_by_sku(self, reservation_id: str, order_id: str, sku: str, quantity: int, causation_id: str = "manual-reservation") -> dict:
        """Сохраняет совместимую ручную команду резерва через факт Inventory."""
        return self.reserve_for_line(order_id, f"manual-{reservation_id}", sku, reservation_id, quantity, causation_id)


class PaymentApplicationService:
    """Выполняет команды одного платежного агрегата."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Сохраняет фабрику Unit of Work."""
        self._unit_of_work_factory = unit_of_work_factory

    def authorize(self, payment_id: str, order_id: str, amount: str, currency: str, causation_id: str = "manual-authorization") -> dict:
        """Создаёт и авторизует платёж, публикуя факт только от корня."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().payment()
            found = repo.get(payment_id)
            if found is not None:
                return found[0].to_state()
            payment = Payment.request_authorization(PaymentId(payment_id), OrderReference(order_id), Money(amount, currency))
            payment.authorize()
            uow.save_aggregate("Payment", "Payment", repo, payment, None, order_id, causation_id)
            return payment.to_state()

    def capture(self, payment_id: str, order_id: str | None = None, causation_id: str = "manual-capture") -> dict:
        """Захватывает авторизацию, восстанавливая заказ для ручного пути."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().payment()
            payment, version = _require(repo.get(payment_id))
            effective_order_id = order_id or payment.to_state()["order_id"]
            status = payment.to_state()["status"]
            if status in {"CAPTURED", "CAPTURE_REJECTED", "VOIDED"}:
                return payment.to_state()
            if status != "AUTHORIZED":
                raise DomainRuleViolation("Захват возможен только после авторизации платежа.")
            payment.capture()
            uow.save_aggregate("Payment", "Payment", repo, payment, version, effective_order_id, causation_id)
            return payment.to_state()

    def reject_authorization(self, payment_id: str, order_id: str, reason: str, causation_id: str) -> dict:
        """Фиксирует ответ шлюза, отказавший в авторизации.

        Метод моделирует границу внешнего платёжного шлюза: решение приходит
        извне, а факт отказа создаёт только локальный агрегат ``Payment``.
        """
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().payment()
            found = repo.get(payment_id)
            if found is None:
                payment = Payment.request_authorization(PaymentId(payment_id), OrderReference(order_id), Money("1.00", "RUB"))
                payment.reject_authorization(reason)
                uow.save_aggregate("Payment", "Payment", repo, payment, None, order_id, causation_id)
                return payment.to_state()
            payment, version = found
            if payment.to_state()["status"] == "PENDING":
                payment.reject_authorization(reason)
                uow.save_aggregate("Payment", "Payment", repo, payment, version, order_id, causation_id)
            return payment.to_state()

    def reject_capture(self, payment_id: str, order_id: str, reason: str, causation_id: str) -> dict:
        """Фиксирует отказ захвата после создания отправления."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().payment()
            payment, version = _require(repo.get(payment_id))
            if payment.to_state()["status"] == "AUTHORIZED":
                payment.reject_capture(reason)
                uow.save_aggregate("Payment", "Payment", repo, payment, version, order_id, causation_id)
            return payment.to_state()

    def void(self, payment_id: str, order_id: str, causation_id: str) -> dict:
        """Аннулирует авторизацию либо возвращает уже аннулированный платёж."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().payment()
            payment, version = _require(repo.get(payment_id))
            if payment.to_state()["status"] != "VOIDED":
                payment.void()
                uow.save_aggregate("Payment", "Payment", repo, payment, version, order_id, causation_id)
            return payment.to_state()


class ShippingApplicationService:
    """Выполняет локальные команды контекста Shipping."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, order_shipment_reader: OrderShipmentReader | None = None) -> None:
        """Сохраняет базу, планировщик и необязательную границу Ordering."""
        self._unit_of_work_factory, self._planning = unit_of_work_factory, ShipmentPlanningService()
        self._order_shipment_reader = order_shipment_reader

    def create(self, shipment_id: str, order_state: dict, causation_id: str = "manual-shipment") -> dict:
        """Создаёт отправление из скалярного снимка заказа."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().shipping()
            found = repo.get(shipment_id)
            if found is not None:
                return found[0].to_state()
            shipment = Shipment.create(ShipmentId(shipment_id), OrderReference(order_state["id"]), order_state["address"], order_state["lines"])
            uow.save_aggregate("Shipping", "Shipment", repo, shipment, None, order_state["id"], causation_id)
            return shipment.to_state()

    def create_for_order(self, shipment_id: str, order_id: str, causation_id: str = "manual-shipment") -> dict:
        """Сохраняет ручный путь создания отправления из скалярного контракта."""
        if self._order_shipment_reader is None:
            raise RuntimeError("Для создания отправления нужен читатель контракта заказа.")
        contract = self._order_shipment_reader.shipment_contract(order_id)
        return self.create(shipment_id, {"id": contract.order_id, "address": contract.address, "lines": contract.lines}, causation_id)

    def dispatch_ready(self, shipment_ids: list[str], causation_id: str = "manual-dispatch") -> list[str]:
        """Применяет план к одному отправлению за одну локальную транзакцию."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().shipping()
            roots = [_require(repo.get(shipment_id)) for shipment_id in shipment_ids]
            plan = self._planning.plan([root for root, _ in roots])
        dispatched: list[str] = []
        for shipment_id in plan.shipment_ids:
            with self._unit_of_work_factory.unit_of_work() as uow:
                repo = uow.repositories().shipping()
                shipment, version = _require(repo.get(shipment_id))
                shipment.dispatch()
                uow.save_aggregate("Shipping", "Shipment", repo, shipment, version, shipment.to_state()["order_id"], causation_id)
                dispatched.append(shipment_id)
        return dispatched

    def cancel(self, shipment_id: str, order_id: str, causation_id: str) -> dict:
        """Отменяет созданное, но ещё не переданное отправление."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            repo = uow.repositories().shipping()
            shipment, version = _require(repo.get(shipment_id))
            if shipment.to_state()["status"] == "CREATED":
                shipment.cancel()
                uow.save_aggregate("Shipping", "Shipment", repo, shipment, version, order_id, causation_id)
            return shipment.to_state()
