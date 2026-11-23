"""CLI-входной адаптер демонстрации пяти контекстов."""

from __future__ import annotations

import json

from .application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService, PaymentApplicationService, ShippingApplicationService
from .infrastructure import Database


def main() -> None:
    """Выполняет синхронный сценарий без событийного транспорта."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    inventory, payment = InventoryApplicationService(database), PaymentApplicationService(database)
    shipping = ShippingApplicationService(database, ordering)
    catalog.create_item("catalog-book", "BOOK-DDD", "Реализация DDD", "seller-north", "1890.00", "RUB")
    catalog.create_item("catalog-note", "NOTE-NORTH", "Тетрадь Север", "seller-north", "240.00", "RUB")
    inventory.create_stock_item("stock-book-small", "warehouse-ark", "BOOK-DDD", 2)
    inventory.create_stock_item("stock-book-large", "warehouse-vologda", "BOOK-DDD", 5)
    inventory.create_stock_item("stock-note", "warehouse-ark", "NOTE-NORTH", 4)

    first = ordering.create_order("order-001", "buyer-001", "RUB")
    ordering.add_catalog_line("order-001", "line-001", "catalog-book", 3)
    first = ordering.place("order-001", "Архангельск, Поморская, 18")
    inventory.reserve_by_sku("reservation-001", "order-001", "BOOK-DDD", 3)
    payment.authorize("payment-001", "order-001", "5670.00", "RUB")
    first_shipment = shipping.create("shipment-001", first)

    second = ordering.create_order("order-002", "buyer-002", "RUB")
    ordering.add_catalog_line("order-002", "line-002", "catalog-note", 1)
    second = ordering.place("order-002", "Вологда, Мира, 7")
    inventory.reserve_by_sku("reservation-002", "order-002", "NOTE-NORTH", 1)
    payment.authorize("payment-002", "order-002", "240.00", "RUB")
    second_shipment = shipping.create("shipment-002", second)
    dispatched = shipping.dispatch_ready(["shipment-001", "shipment-002"])
    print(json.dumps({"orders": [first, second], "shipments": [first_shipment, second_shipment], "dispatch_plan": dispatched}, ensure_ascii=False))


if __name__ == "__main__":
    main()
