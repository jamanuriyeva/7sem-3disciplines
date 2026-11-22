import unittest
from decimal import Decimal

from north_marketplace.domain.identifiers import BuyerId, OrderId, OrderLineId, SellerId, Sku
from north_marketplace.domain.order import (
    Order,
    OrderError,
    OrderLineNotFoundError,
    OrderNotDraftError,
    OrderNotReadyError,
    OrderStatus,
)
from north_marketplace.domain.values import Currency, Money, Quantity, ShippingAddress


class OrderBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rub = Currency("RUB")
        self.order = Order(OrderId("order-1"), BuyerId("buyer-1"), self.rub)
        self.seller = SellerId("seller-1")

    def _address(self) -> ShippingAddress:
        return ShippingAddress("Архангельск", "Поморская", "18", "163000")

    def test_line_id_changes_quantity_and_place_requires_full_order(self) -> None:
        line_id = OrderLineId("line-1")
        self.order.add_paid_line(
            line_id, Sku("BOOK-001"), "Книга о Севере", self.seller,
            Money(Decimal("100.00"), self.rub), Quantity(1),
        )

        with self.assertRaises(OrderNotReadyError):
            self.order.place()

        self.order.set_shipping_address(self._address())
        self.order.change_line_quantity(line_id, Quantity(3))
        self.assertEqual(self.order.lines()[0].quantity, Quantity(3))
        self.assertEqual(self.order.total(), Money(Decimal("300.00"), self.rub))

        self.order.place()
        self.assertIs(self.order.status(), OrderStatus.PLACED)

        with self.assertRaises(OrderNotDraftError):
            self.order.add_paid_line(
                OrderLineId("line-2"), Sku("BOOK-002"), "Ещё книга", self.seller,
                Money(Decimal("50.00"), self.rub), Quantity(1),
            )
        with self.assertRaises(OrderNotDraftError):
            self.order.set_shipping_address(self._address())
        with self.assertRaises(OrderNotDraftError):
            self.order.change_line_quantity(line_id, Quantity(1))

    def test_empty_order_cannot_be_placed(self) -> None:
        self.order.set_shipping_address(self._address())
        with self.assertRaises(OrderNotReadyError):
            self.order.place()

    def test_missing_line_id_raises(self) -> None:
        with self.assertRaises(OrderLineNotFoundError):
            self.order.change_line_quantity(OrderLineId("nope"), Quantity(1))

    def test_currency_mismatch_rejected(self) -> None:
        usd = Currency("USD")
        with self.assertRaises(OrderError):
            self.order.add_paid_line(
                OrderLineId("line-1"), Sku("BOOK-001"), "Книга", self.seller,
                Money(Decimal("10.00"), usd), Quantity(1),
            )