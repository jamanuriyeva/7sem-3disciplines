"""CLI-вход для наблюдаемого polling-процесса обеспечения заказа."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService
from .fulfillment_application import FulfillmentApplicationService
from .infrastructure import Database


def _database(path: str, reset: bool) -> Database:
    """Открывает SQLite-базу и по запросу создаёт чистый демонстрационный запуск."""
    file = Path(path)
    if reset and file.exists():
        file.unlink()
    return Database(f"sqlite+pysqlite:///{file}")


def _prepare(database: Database, scenario: str) -> dict:
    """Фиксирует заказ и ``OrderPlaced``, не продвигая уведомления.

    Raises:
        ValueError: Демонстрационный заказ уже существует в выбранной базе.
    """
    catalog = CatalogApplicationService(database)
    inventory = InventoryApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    try:
        ordering.get("order-001")
    except KeyError:
        pass
    else:
        raise ValueError("Демонстрационный заказ уже существует. Укажите --reset или другую базу.")
    catalog.create_item("catalog-book", "BOOK-DDD", "Реализация DDD", "seller-north", "1890.00", "RUB")
    catalog.create_item("catalog-mark", "MARK-NORTH", "Закладка Север", "seller-north", "1.00", "RUB")
    inventory.create_stock_item("stock-book", "warehouse-ark", "BOOK-DDD", 2)
    inventory.create_stock_item("stock-mark", "warehouse-ark", "MARK-NORTH", 2 if scenario == "success" else 1)
    ordering.create_order("order-001", "buyer-001", "RUB")
    ordering.add_catalog_line("order-001", "line-book", "catalog-book", 1)
    ordering.add_catalog_line("order-001", "line-mark", "catalog-mark", 1 if scenario == "success" else 2)
    return ordering.place("order-001", "Архангельск, Поморская, 18")


def _state(database: Database) -> dict:
    """Возвращает текущее состояние заказа и сохраняемого процесса."""
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    fulfillment = FulfillmentApplicationService(database, database)
    return {"order": ordering.get("order-001"), "process": fulfillment.state("order-001")}


def main() -> None:
    """Запускает один явный шаг polling-процесса или совместимый demo-маршрут."""
    parser = argparse.ArgumentParser(description="Север: polling-процесс обеспечения заказа")
    parser.add_argument("command", nargs="?", choices=["prepare", "dispatch", "resume", "state", "demo"], default="demo")
    parser.add_argument("--scenario", choices=["success", "failure"], default="success")
    parser.add_argument("--database", default="north-stage-03.db")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-notifications", type=int, default=None)
    parser.add_argument("--reset", action="store_true")
    arguments = parser.parse_args()
    database = _database(arguments.database, arguments.reset)
    limit = arguments.max_notifications if arguments.max_notifications is not None else arguments.limit
    fulfillment = FulfillmentApplicationService(database, database)

    if arguments.command == "prepare":
        print(json.dumps({"order": _prepare(database, arguments.scenario)}, ensure_ascii=False))
        return
    if arguments.command == "dispatch":
        print(json.dumps({"delivered": fulfillment.dispatcher().dispatch(limit)}, ensure_ascii=False))
        return
    if arguments.command == "resume":
        print(json.dumps({"executed": fulfillment.dispatch_commands(limit)}, ensure_ascii=False))
        return
    if arguments.command == "state":
        print(json.dumps(_state(database), ensure_ascii=False))
        return

    _prepare(database, arguments.scenario)
    while fulfillment.resume(limit):
        pass
    print(json.dumps(_state(database), ensure_ascii=False))


if __name__ == "__main__":
    main()
