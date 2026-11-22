from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from north_marketplace.domain.identifiers import (
    BuyerId, OrderId, OrderLineId, ReservationId, SellerId, Sku,
    StockItemId, WarehouseId,
)
from north_marketplace.domain.order import Order
from north_marketplace.domain.stock_item import StockItem
from north_marketplace.domain.values import (
    Currency, Money, Quantity, ReservationDeadline, ShippingAddress, StockBalance,
)


def run_demo() -> str:
    rub = Currency("RUB")

    order = Order(OrderId("order-100"), BuyerId("buyer-20"), rub)
    seller = SellerId("seller-7")
    order.add_paid_line(
        OrderLineId("line-1"), Sku("BOOK-001"), "Книга о Севере", seller,
        Money(Decimal("1290.00"), rub), Quantity(1),
    )
    order.add_gift_line(
        OrderLineId("line-2"), Sku("BOOKMARK-001"), "Закладка", seller, Quantity(1),
    )
    order.change_line_quantity(OrderLineId("line-1"), Quantity(2))
    order.set_shipping_address(
        ShippingAddress("Архангельск", "Поморская", "18", "163000")
    )
    order.place()

    stock_item = StockItem(
        StockItemId("stock-400"), WarehouseId("warehouse-ark"),
        Sku("BOOK-001"), StockBalance(8),
    )
    stock_item.reserve(
        ReservationId("reservation-900"),
        order.id(),
        Quantity(1),
        ReservationDeadline(datetime(2030, 1, 1)),
    )

    return (
        f"Заказ: статус={order.status().value}, "
        f"сумма={order.total()}, "
        f"физический остаток={stock_item.physical_balance()}, "
        f"доступный остаток={stock_item.available_quantity()}, "
        f"резервы по OrderId={order.id()}"
    )