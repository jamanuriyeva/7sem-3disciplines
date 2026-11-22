import unittest
from decimal import Decimal

from north_marketplace.domain.identifiers import (
    BuyerId, OrderId, OrderLineId, SellerId, Sku,
)
from north_marketplace.domain.order import LineKind, Order, OrderNotReadyError
from north_marketplace.domain.values import Currency, Money, Quantity


class OrderGiftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rub = Currency("RUB")
        self.order = Order(OrderId("order-1"), BuyerId("buyer-1"), self.rub)
        self.seller = SellerId("seller-a")

    def test_gift_does_not_replace_a_paid_line_and_lines_are_snapshots(self) -> None:
        self.order.add_gift_line(
            OrderLineId("gift-1"), Sku("MARK-1"), "Закладка", self.seller, Quantity(1)
        )
        snap = self.order.lines()[0]
        self.assertIs(snap.kind, LineKind.GIFT)
        self.assertEqual(snap.unit_price, Money(Decimal("0"), self.rub))

        self.order.add_paid_line(
            OrderLineId("paid-1"), Sku("BOOK-1"), "Книга", self.seller,
            Money(Decimal("1290.00"), self.rub), Quantity(1),
        )
        self.assertEqual(len(self.order.lines()), 2)
        self.assertEqual(self.order.total(), Money(Decimal("1290.00"), self.rub))

        self.order.set_shipping_address(
            __import__("north_marketplace.domain.values", fromlist=["ShippingAddress"])
            .ShippingAddress("Архангельск", "Поморская", "18", "163000")
        )
        self.order.place()