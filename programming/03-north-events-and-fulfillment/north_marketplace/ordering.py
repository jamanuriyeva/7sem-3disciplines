"""Контекст Ordering: заказ хранит собственные снимки предложений."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from .events import OrderFulfilled, OrderFulfillmentRejected, OrderPlaced, RaisesDomainEvents
from .kernel import BuyerId, DomainRuleViolation, Identifier, Money, Quantity, SellerId, Sku


class OrderId(Identifier):
    """Идентификатор корня заказа."""


class OrderLineId(Identifier):
    """Идентификатор внутренней строки заказа."""


class CatalogOfferSnapshot:
    """Локальный снимок опубликованного предложения без связи с Catalog."""

    def __init__(self, sku: Sku, title: str, seller_id: SellerId, unit_price: Money) -> None:
        """Создаёт значение, достаточное для строки заказа."""
        if not isinstance(title, str) or not title.strip() or unit_price.amount() <= 0:
            raise DomainRuleViolation("Снимок предложения требует названия и положительной цены.")
        self._sku, self._title = sku, title.strip()
        self._seller_id, self._unit_price = seller_id, unit_price

    def seller_id(self) -> SellerId:
        """Возвращает зафиксированного продавца."""
        return self._seller_id

    def currency(self) -> str:
        """Возвращает валюту зафиксированной цены."""
        return self._unit_price.currency()

    def unit_price(self) -> Money:
        """Возвращает цену одной единицы."""
        return self._unit_price

    def to_state(self) -> dict:
        """Возвращает скалярное состояние снимка."""
        return {"sku": self._sku.value(), "title": self._title, "seller_id": self._seller_id.value(), "amount": str(self._unit_price.amount()), "currency": self._unit_price.currency()}

    @classmethod
    def from_state(cls, state: dict) -> "CatalogOfferSnapshot":
        """Восстанавливает снимок без импорта модели Catalog."""
        return cls(Sku(state["sku"]), state["title"], SellerId(state["seller_id"]), Money(state["amount"], state["currency"]))


class OrderStatus(Enum):
    """Состояния заказа вместе с итогом обеспечения."""

    DRAFT = "DRAFT"
    PLACED = "PLACED"
    CANCELLED = "CANCELLED"
    FULFILLED = "FULFILLED"
    FULFILLMENT_REJECTED = "FULFILLMENT_REJECTED"


class OrderLine:
    """Внутренняя сущность со снимком предложения и количества."""

    def __init__(self, line_id: OrderLineId, quote: CatalogOfferSnapshot, quantity: Quantity) -> None:
        """Создаёт строку из ценового снимка."""
        self._id, self._quote, self._quantity = line_id, quote, quantity

    def id(self) -> OrderLineId:
        """Возвращает идентичность строки."""
        return self._id

    def seller_id(self) -> SellerId:
        """Возвращает продавца, зафиксированного снимком."""
        return self._quote.seller_id()

    def currency(self) -> str:
        """Возвращает валюту ценового снимка."""
        return self._quote.currency()

    def subtotal(self) -> Money:
        """Вычисляет стоимость строки."""
        return self._quote.unit_price().multiply(self._quantity)

    def to_state(self) -> dict:
        """Сериализует внутреннюю сущность без ссылки на CatalogItem."""
        state = self._quote.to_state()
        state.update({"line_id": self._id.value(), "quantity": self._quantity.value()})
        return state

    @classmethod
    def from_state(cls, state: dict) -> "OrderLine":
        """Восстанавливает строку из сохранённого снимка."""
        return cls(OrderLineId(state["line_id"]), CatalogOfferSnapshot.from_state(state), Quantity(state["quantity"]))


class Order(RaisesDomainEvents):
    """Корень заказа, владеющий единой валютой, продавцом и строками."""

    def __init__(self, order_id: OrderId, buyer_id: BuyerId, currency: str, status: OrderStatus = OrderStatus.DRAFT, address: str | None = None, lines: list[OrderLine] | None = None) -> None:
        """Создаёт или восстанавливает заказ."""
        self._id, self._buyer_id = order_id, buyer_id
        self._start_event_recording()
        self._currency = Money("0", currency).currency()
        self._status, self._address, self._lines = status, address, list(lines or [])
        self._seller_id = self._lines[0].seller_id() if self._lines else None

    @classmethod
    def create_draft(cls, order_id: OrderId, buyer_id: BuyerId, currency: str) -> "Order":
        """Создаёт единственную допустимую начальную форму заказа."""
        return cls(order_id, buyer_id, currency)

    def add_line(self, line_id: OrderLineId, quote: CatalogOfferSnapshot, quantity: Quantity) -> None:
        """Добавляет строку и фиксирует продавца первой строкой.

        Raises:
            DomainRuleViolation: Черновик закрыт, валюта или продавец отличаются.
        """
        self._require_draft()
        line = OrderLine(line_id, quote, quantity)
        if line.currency() != self._currency:
            raise DomainRuleViolation("Валюта строки не совпадает с валютой заказа.")
        if any(existing.id() == line_id for existing in self._lines):
            raise DomainRuleViolation("Идентификатор строки уже существует.")
        if self._seller_id is not None and line.seller_id() != self._seller_id:
            raise DomainRuleViolation("Один заказ относится к одному продавцу.")
        self._seller_id = line.seller_id()
        self._lines.append(line)

    def set_shipping_address(self, address: str) -> None:
        """Фиксирует адрес до размещения."""
        self._require_draft()
        if not isinstance(address, str) or not address.strip():
            raise DomainRuleViolation("Адрес доставки обязателен.")
        self._address = address.strip()

    def place(self) -> None:
        """Размещает полный заказ с положительной суммой."""
        self._require_draft()
        if not self._lines or not self._address or self.total().amount() <= 0:
            raise DomainRuleViolation("Заказу нужны строки, адрес и положительная сумма.")
        self._status = OrderStatus.PLACED
        self._record(OrderPlaced(self.to_state()))

    def fulfill(self) -> None:
        """Фиксирует успешное обеспечение уже размещённого заказа."""
        if self._status is not OrderStatus.PLACED:
            raise DomainRuleViolation("Завершить можно только размещённый заказ.")
        self._status = OrderStatus.FULFILLED
        self._record(OrderFulfilled({"order_id": self._id.value()}))

    def reject_fulfillment(self, reason: str) -> None:
        """Фиксирует итоговый отказ после выполненной компенсации."""
        if self._status is not OrderStatus.PLACED:
            raise DomainRuleViolation("Отклонить можно только размещённый заказ.")
        self._status = OrderStatus.FULFILLMENT_REJECTED
        self._record(OrderFulfillmentRejected({"order_id": self._id.value(), "reason": reason}))

    def cancel(self) -> None:
        """Отменяет заказ, если он ещё не отменён."""
        if self._status is OrderStatus.CANCELLED:
            raise DomainRuleViolation("Заказ уже отменён.")
        self._status = OrderStatus.CANCELLED

    def total(self) -> Money:
        """Возвращает сумму строк в валюте заказа."""
        return Money(sum((line.subtotal().amount() for line in self._lines), Decimal("0")), self._currency)

    def id(self) -> OrderId:
        """Возвращает идентификатор корня."""
        return self._id

    def to_state(self) -> dict:
        """Подготавливает снимок актуального состояния для Data Mapper."""
        return {"id": self._id.value(), "buyer_id": self._buyer_id.value(), "currency": self._currency, "status": self._status.value, "address": self._address, "lines": [line.to_state() for line in self._lines]}

    @classmethod
    def from_state(cls, state: dict) -> "Order":
        """Восстанавливает корень и внутренние сущности из строки хранения."""
        return cls(OrderId(state["id"]), BuyerId(state["buyer_id"]), state["currency"], OrderStatus(state["status"]), state["address"], [OrderLine.from_state(line) for line in state["lines"]])

    def _require_draft(self) -> None:
        """Проверяет предусловие изменения заказа."""
        if self._status is not OrderStatus.DRAFT:
            raise DomainRuleViolation("Изменять можно только черновик заказа.")
