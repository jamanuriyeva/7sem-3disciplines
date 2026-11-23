"""Проверки границ контекстов и фактов, создаваемых агрегатами."""

import ast
from pathlib import Path

from north_marketplace.application import CatalogApplicationService, OrderingApplicationService
from north_marketplace.infrastructure import Database
from north_marketplace.kernel import ConcurrencyConflict


def test_ordering_domain_does_not_import_catalog_model() -> None:
    """Ordering получает снимок через прикладной перевод, а не импорт Catalog."""
    source = Path(__file__).parents[1] / "north_marketplace" / "ordering.py"
    imported = {node.module for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))) if isinstance(node, ast.ImportFrom)}
    assert ".catalog" not in imported


def test_place_records_order_placed_with_current_state() -> None:
    """Команда корня одновременно меняет заказ и записывает факт в журнал."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("catalog-1", "BOOK", "Книга", "seller-1", "10.00", "RUB")
    ordering.create_order("order-1", "buyer-1", "RUB")
    ordering.add_catalog_line("order-1", "line-1", "catalog-1", 1)
    ordering.place("order-1", "Архангельск, 1")
    notifications = database.notifications_for("probe", {"OrderPlaced"}, 10)
    assert len(notifications) == 1
    assert notifications[0].payload["id"] == "order-1"


def test_current_state_repository_rejects_stale_aggregate_version() -> None:
    """Журнал фактов не отменяет необходимость CAS для актуального состояния."""
    database = Database("sqlite+pysqlite:///:memory:")
    ordering = OrderingApplicationService(database)
    ordering.create_order("order-1", "buyer", "RUB")
    with database.unit_of_work() as uow:
        first, version = uow.repositories()["ordering"].get("order-1")
    with database.unit_of_work() as uow:
        second, current_version = uow.repositories()["ordering"].get("order-1")
        second.set_shipping_address("Архангельск, 1")
        uow.repositories()["ordering"].save(second, current_version)
    first.set_shipping_address("Мурманск, 2")
    with database.unit_of_work() as uow:
        try:
            uow.repositories()["ordering"].save(first, version)
        except ConcurrencyConflict:
            pass
        else:
            raise AssertionError("Старая версия не была отклонена.")


def test_inherited_stage_02_order_path_preserves_price_snapshot() -> None:
    """Событийная стадия сохраняет сценарий заказа и добавляет факт после commit."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("catalog-2", "BOOK", "Книга", "seller", "10.00", "RUB")
    ordering.create_order("order-2", "buyer", "RUB")
    ordering.add_catalog_line("order-2", "line-2", "catalog-2", 2)
    placed = ordering.place("order-2", "Архангельск, 2")
    assert placed["status"] == "PLACED"
    assert placed["lines"][0]["amount"] == "10.00"
    assert database.notifications_for("lineage", {"OrderPlaced"}, 1)[0].aggregate_id == "order-2"
