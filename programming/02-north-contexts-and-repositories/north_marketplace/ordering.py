"""Контекст Ordering: собственные снимки предложений и правила заказа.

Модуль намеренно не импортирует модель Catalog. Прикладной слой переводит
скалярный контракт каталога в локальный ``CatalogOfferSnapshot`` до вызова
корня ``Order``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from .kernel import BuyerId, DomainRuleViolation, Identifier, Money, Quantity, SellerId, Sku


class OrderId(Identifier):
    """Идентифицирует корень агрегата заказа."""


class OrderLineId(Identifier):
    """Идентифицирует внутреннюю сущность строки заказа."""


class CatalogOfferSnapshot:
    """Локальный объект-значение с зафиксированными данными предложения.

    Контекст Ordering владеет этим снимком после перевода опубликованного
    каталожного контракта. Изменение ``CatalogItem`` не изменяет уже созданную
    строку заказа.
    """

    def __init__(self, sku: Sku, title: str, seller_id: SellerId, unit_price: Money) -> None:
        """Создаёт допустимый снимок предложения.

        Args:
            sku: Код продаваемой позиции.
            title: Название, видимое при оформлении.
            seller_id: Идентификатор продавца из опубликованного контракта.
            unit_price: Положительная цена в валюте заказа.

        Raises:
            DomainRuleViolation: Название пусто либо цена неположительна.
        """
        if not isinstance(title, str) or not title.strip():
            raise DomainRuleViolation("Снимку предложения нужно название.")
        if unit_price.amount() <= 0:
            raise DomainRuleViolation("Снимок предложения требует положительную цену.")
        self._sku = sku
        self._title = title.strip()
        self._seller_id = seller_id
        self._unit_price = unit_price

    def seller_id(self) -> SellerId:
        """Возвращает продавца, зафиксированного в локальном снимке."""
        return self._seller_id

    def currency(self) -> str:
        """Возвращает валюту зафиксированной цены."""
        return self._unit_price.currency()

    def unit_price(self) -> Money:
        """Возвращает неизменяемую цену одной единицы."""
        return self._unit_price

    def to_state(self) -> dict:
        """Возвращает скалярное представление для строки заказа."""
        return {
            "sku": self._sku.value(),
            "title": self._title,
            "seller_id": self._seller_id.value(),
            "amount": str(self._unit_price.amount()),
            "currency": self._unit_price.currency(),
        }

    @classmethod
    def from_state(cls, state: dict) -> "CatalogOfferSnapshot":
        """Восстанавливает локальный снимок без обращения к Catalog."""
        return cls(Sku(state["sku"]), state["title"], SellerId(state["seller_id"]), Money(state["amount"], state["currency"]))


class OrderStatus(Enum):
    """Состояния заказа до введения межконтекстного процесса."""

    DRAFT = "DRAFT"
    PLACED = "PLACED"
    CANCELLED = "CANCELLED"


class OrderLine:
    """Внутренняя сущность со снимком предложения и количеством."""

    def __init__(self, line_id: OrderLineId, offer: CatalogOfferSnapshot, quantity: Quantity) -> None:
        """Создаёт строку из уже переведённого локального значения."""
        self._id, self._offer, self._quantity = line_id, offer, quantity

    def id(self) -> OrderLineId:
        """Возвращает идентичность строки внутри заказа."""
        return self._id

    def seller_id(self) -> SellerId:
        """Возвращает продавца снимка."""
        return self._offer.seller_id()

    def currency(self) -> str:
        """Возвращает валюту цены строки."""
        return self._offer.currency()

    def subtotal(self) -> Money:
        """Вычисляет стоимость строки из зафиксированной цены."""
        return self._offer.unit_price().multiply(self._quantity)

    def to_state(self) -> dict:
        """Сериализует строку без модели чужого контекста."""
        state = self._offer.to_state()
        state.update({"line_id": self._id.value(), "quantity": self._quantity.value()})
        return state

    @classmethod
    def from_state(cls, state: dict) -> "OrderLine":
        """Восстанавливает строку и локальный снимок из текущего состояния."""
        return cls(OrderLineId(state["line_id"]), CatalogOfferSnapshot.from_state(state), Quantity(state["quantity"]))


class Order:
    """Корень заказа, владеющий валютой, продавцом и внутренними строками."""

    def __init__(self, order_id: OrderId, buyer_id: BuyerId, currency: str, status: OrderStatus = OrderStatus.DRAFT, address: str | None = None, lines: list[OrderLine] | None = None) -> None:
        """Создаёт или восстанавливает заказ."""
        self._id, self._buyer_id = order_id, buyer_id
        self._currency = Money("0", currency).currency()
        self._status, self._address, self._lines = status, address, list(lines or [])
        self._seller_id = self._lines[0].seller_id() if self._lines else None

    @classmethod
    def create_draft(cls, order_id: OrderId, buyer_id: BuyerId, currency: str) -> "Order":
        """Создаёт единственную допустимую начальную форму заказа."""
        return cls(order_id, buyer_id, currency)

    def add_line(self, line_id: OrderLineId, offer: CatalogOfferSnapshot, quantity: Quantity) -> None:
        """Добавляет строку и фиксирует продавца первой строкой.

        Args:
            line_id: Уникальная идентичность новой строки.
            offer: Локальный снимок опубликованного предложения.
            quantity: Требуемое положительное количество.

        Raises:
            DomainRuleViolation: Черновик закрыт, валюта или продавец отличаются.
        """
        self._require_draft()
        line = OrderLine(line_id, offer, quantity)
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
        """Подготавливает актуальное состояние для Data Mapper."""
        return {"id": self._id.value(), "buyer_id": self._buyer_id.value(), "currency": self._currency, "status": self._status.value, "address": self._address, "lines": [line.to_state() for line in self._lines]}

    @classmethod
    def from_state(cls, state: dict) -> "Order":
        """Восстанавливает корень и внутренние сущности из строки хранения."""
        return cls(OrderId(state["id"]), BuyerId(state["buyer_id"]), state["currency"], OrderStatus(state["status"]), state["address"], [OrderLine.from_state(line) for line in state["lines"]])

    def _require_draft(self) -> None:
        """Проверяет предусловие изменения заказа."""
        if self._status is not OrderStatus.DRAFT:
            raise DomainRuleViolation("Изменять можно только черновик заказа.")
