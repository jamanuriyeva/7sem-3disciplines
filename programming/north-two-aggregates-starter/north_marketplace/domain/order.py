from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .identifiers import BuyerId, OrderId, OrderLineId, SellerId, Sku
from .values import Currency, Money, Quantity, ShippingAddress


class OrderError(Exception):
    """Base for domain errors of the Order aggregate."""


class OrderNotDraftError(OrderError):
    pass


class DuplicateOrderLineError(OrderError):
    pass


class OrderLineNotFoundError(OrderError):
    pass


class OrderNotReadyError(OrderError):
    pass


class SellerMismatchError(OrderError):
    pass


class OrderErrorCurrencyMismatch(OrderError):
    pass


class LineKind(Enum):
    PAID = "PAID"
    GIFT = "GIFT"


class OrderStatus(Enum):
    DRAFT = "DRAFT"
    PLACED = "PLACED"
    CANCELLED = "CANCELLED"


class OrderLine:
    def __init__(
        self,
        line_id: OrderLineId,
        sku: Sku,
        title: str,
        seller_id: SellerId,
        unit_price: Money,
        quantity: Quantity,
        kind: LineKind,
    ) -> None:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be non-empty")
        self._id = line_id
        self._sku = sku
        self._title = title
        self._seller_id = seller_id
        self._unit_price = unit_price
        self._quantity = quantity
        self._kind = kind

    def _change_quantity(self, quantity: Quantity) -> None:
        self._quantity = quantity

    def _snapshot(self) -> "OrderLineSnapshot":
        return OrderLineSnapshot(
            id=self._id,
            sku=self._sku,
            title=self._title,
            seller_id=self._seller_id,
            unit_price=self._unit_price,
            quantity=self._quantity,
            kind=self._kind,
        )


@dataclass(frozen=True)
class OrderLineSnapshot:
    id: OrderLineId
    sku: Sku
    title: str
    seller_id: SellerId
    unit_price: Money
    quantity: Quantity
    kind: LineKind

    def subtotal(self) -> Money:
        return self.unit_price * self.quantity.value


class Order:
    def __init__(self, order_id: OrderId, buyer_id: BuyerId, currency: Currency) -> None:
        self._id = order_id
        self._buyer_id = buyer_id
        self._currency = currency
        self._status = OrderStatus.DRAFT
        self._lines: list[OrderLine] = []
        self._shipping_address: ShippingAddress | None = None
        self._seller_id: SellerId | None = None

    def id(self) -> OrderId:
        return self._id

    def buyer_id(self) -> BuyerId:
        return self._buyer_id

    def currency(self) -> Currency:
        return self._currency

    def status(self) -> OrderStatus:
        return self._status

    def lines(self) -> tuple[OrderLineSnapshot, ...]:
        return tuple(line._snapshot() for line in self._lines)

    def shipping_address(self) -> ShippingAddress | None:
        return self._shipping_address

    def seller_id(self) -> SellerId | None:
        return self._seller_id

    def total(self) -> Money:
        result = Money(Decimal("0"), self._currency)
        for line in self._lines:
            result = result + line._snapshot().subtotal()
        return result

    def add_paid_line(
        self,
        line_id: OrderLineId,
        sku: Sku,
        title: str,
        seller_id: SellerId,
        price: Money,
        quantity: Quantity,
    ) -> None:
        self._require_draft()
        self._require_unique_line_id(line_id)
        self._require_same_seller(seller_id)
        if price.currency != self._currency:
            raise OrderErrorCurrencyMismatch("Line currency must match order currency")
        if price.amount <= 0:
            raise OrderError("Paid line price must be positive")
        self._lines.append(
            OrderLine(line_id, sku, title, seller_id, price, quantity, LineKind.PAID)
        )
        if self._seller_id is None:
            self._seller_id = seller_id

    def add_gift_line(
        self,
        line_id: OrderLineId,
        sku: Sku,
        title: str,
        seller_id: SellerId,
        quantity: Quantity,
    ) -> None:
        self._require_draft()
        self._require_unique_line_id(line_id)
        self._require_same_seller(seller_id)
        gift_price = Money(Decimal("0"), self._currency)
        self._lines.append(
            OrderLine(line_id, sku, title, seller_id, gift_price, quantity, LineKind.GIFT)
        )
        if self._seller_id is None:
            self._seller_id = seller_id

    def change_line_quantity(self, line_id: OrderLineId, quantity: Quantity) -> None:
        self._require_draft()
        line = self._find_line(line_id)
        line._change_quantity(quantity)

    def set_shipping_address(self, address: ShippingAddress) -> None:
        self._require_draft()
        self._shipping_address = address

    def place(self) -> None:
        self._require_draft()
        if self._shipping_address is None:
            raise OrderNotReadyError("Shipping address is required")
        if not self._lines:
            raise OrderNotReadyError("At least one line is required")
        if not any(line._kind is LineKind.PAID for line in self._lines):
            raise OrderNotReadyError("At least one paid line is required")
        if self.total().amount <= 0:
            raise OrderNotReadyError("Total must be positive")
        self._status = OrderStatus.PLACED

    def cancel(self) -> None:
        if self._status is OrderStatus.CANCELLED:
            raise OrderError("Order already cancelled")
        self._status = OrderStatus.CANCELLED

    def _require_draft(self) -> None:
        if self._status is not OrderStatus.DRAFT:
            raise OrderNotDraftError("Order is not a draft")

    def _require_unique_line_id(self, line_id: OrderLineId) -> None:
        if any(line._id == line_id for line in self._lines):
            raise DuplicateOrderLineError(f"Duplicate OrderLineId: {line_id}")

    def _require_same_seller(self, seller_id: SellerId) -> None:
        if self._seller_id is not None and self._seller_id != seller_id:
            raise SellerMismatchError(
                f"All lines must belong to one seller: {self._seller_id} != {seller_id}"
            )

    def _find_line(self, line_id: OrderLineId) -> OrderLine:
        for line in self._lines:
            if line._id == line_id:
                return line
        raise OrderLineNotFoundError(f"OrderLineId not found: {line_id}")