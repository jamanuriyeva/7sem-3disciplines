import unittest

from north_marketplace.application import run_demo


class DemoTests(unittest.TestCase):
    def test_cli_coordinates_order_and_stock_item_through_order_id(self) -> None:
        result = run_demo()
        self.assertIn("PLACED", result)
        self.assertIn("2580.00 RUB", result)
        self.assertIn("физический остаток=8", result)
        self.assertIn("доступный остаток=7", result)
        self.assertIn("order-100", result)