import unittest
from datetime import datetime

from north_marketplace.domain.identifiers import OrderId, ReservationId, Sku, StockItemId, WarehouseId
from north_marketplace.domain.stock_item import (
    ReservationStatus, StockItem,
)
from north_marketplace.domain.values import Quantity, ReservationDeadline, StockBalance


class StockExpirationTests(unittest.TestCase):
    def test_due_reservation_expires_and_releases_balance(self) -> None:
        stock = StockItem(
            StockItemId("stock-1"), WarehouseId("w-1"), Sku("BOOK-1"), StockBalance(5)
        )
        stock.reserve(
            ReservationId("res-1"), OrderId("order-1"), Quantity(2),
            ReservationDeadline(datetime(2030, 1, 1)),
        )

        # Будущий срок — не истекает
        self.assertEqual(stock.expire_reservations(datetime(2029, 1, 1)), 0)
        self.assertEqual(stock.available_quantity(), StockBalance(3))

        # Наступивший срок — истекает
        self.assertEqual(stock.expire_reservations(datetime(2030, 1, 2)), 1)
        self.assertEqual(stock.available_quantity(), StockBalance(5))
        self.assertIs(stock.reservations()[0].status, ReservationStatus.EXPIRED)

        # Повторно не истекает
        self.assertEqual(stock.expire_reservations(datetime(2031, 1, 1)), 0)