import unittest

from north_marketplace.domain.identifiers import Sku, StockItemId, WarehouseId
from north_marketplace.domain.stock_item import InsufficientStockError, StockItem
from north_marketplace.domain.values import Quantity, StockBalance


class StockBaselineTests(unittest.TestCase):
    def test_physical_balance_never_becomes_negative(self) -> None:
        stock = StockItem(
            StockItemId("stock-1"), WarehouseId("w-1"), Sku("BOOK-1"), StockBalance(5)
        )
        stock.receive(Quantity(3))
        self.assertEqual(stock.physical_balance(), StockBalance(8))

        stock.write_off(Quantity(5))
        self.assertEqual(stock.physical_balance(), StockBalance(3))

        with self.assertRaises(InsufficientStockError):
            stock.write_off(Quantity(10))
        self.assertEqual(stock.physical_balance(), StockBalance(3))