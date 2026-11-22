import unittest
from datetime import datetime, timedelta

from north_marketplace.domain.identifiers import OrderId, ReservationId, Sku, StockItemId, WarehouseId
from north_marketplace.domain.stock_item import (
    InsufficientStockError, ReservationStatus, StockItem,
)
from north_marketplace.domain.values import Quantity, ReservationDeadline, StockBalance


class StockReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stock = StockItem(
            StockItemId("stock-1"), WarehouseId("w-1"), Sku("BOOK-1"), StockBalance(5)
        )
        self.deadline = ReservationDeadline(datetime(2030, 1, 1))

    def test_active_reservations_do_not_exceed_available_balance(self) -> None:
        self.stock.reserve(
            ReservationId("res-1"), OrderId("order-1"), Quantity(3), self.deadline
        )
        self.assertEqual(self.stock.available_quantity(), StockBalance(2))

        with self.assertRaises(InsufficientStockError):
            self.stock.reserve(
                ReservationId("res-2"), OrderId("order-2"), Quantity(3), self.deadline
            )

        self.stock.release_reservation(ReservationId("res-1"))
        self.assertEqual(self.stock.available_quantity(), StockBalance(5))
        self.assertEqual(len(self.stock.reservations()), 1)
        self.assertIs(self.stock.reservations()[0].status, ReservationStatus.RELEASED)