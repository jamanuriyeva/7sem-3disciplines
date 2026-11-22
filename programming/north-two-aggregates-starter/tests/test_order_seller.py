import unittest
from decimal import Decimal

from north_marketplace.domain.identifiers import (
    BuyerId, OrderId, OrderLineId, SellerId, Sku,
)
from north_marketplace.domain.order import (
    DuplicateOrderLineError, Order, SellerMismatchError,
)
from north_marketplace.domain.values import Currency, Money, Quantity


class OrderSellerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rub = Currency("RUB")
        self.order = Order(OrderId("order-1"), BuyerId("buyer-1"), self.rub)
        self.seller_a = SellerId("seller-a")
        self.seller_b = SellerId("seller-b")

    def _paid(self, line_id: str, seller: SellerId, amount: str = "100.00") -> None:
        self.order.add_paid_line(
            OrderLineId(line_id), Sku("BOOK-001"), "Книга", seller,
            Money(Decimal(amount), self.rub), Quantity(1),
        )

    def test_line_id_is_unique_and_first_seller_rejects_another_seller(self) -> None:
        self._paid("line-1", self.seller_a)
        self.assertEqual(self.order.seller_id(), self.seller_a)

        before = self.order.lines()
        with self.assertRaises(SellerMismatchError):
            self._paid("line-2", self.seller_b)
        self.assertEqual(self.order.lines(), before)

        with self.assertRaises(DuplicateOrderLineError):
            self._paid("line-1", self.seller_a)
        self.assertEqual(self.order.lines(), before)

        self._paid("line-3", self.seller_a)
        self.assertEqual(len(self.order.lines()), 2)