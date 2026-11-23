"""Проверки агрегатов, Data Mapper и HTTP-границы второй стадии."""

from fastapi.testclient import TestClient
import pytest

from north_marketplace.api import create_app
from north_marketplace.application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService, PaymentApplicationService, ShippingApplicationService
from north_marketplace.infrastructure import Database, repositories
from north_marketplace.kernel import ConcurrencyConflict, DomainRuleViolation


def test_repository_restores_plain_order_aggregate() -> None:
    """Data Mapper восстанавливает обычный Order, а не ORM-строку."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("c-1", "BOOK", "Книга", "seller-1", "10.00", "RUB")
    ordering.create_order("o-1", "buyer-1", "RUB")
    ordering.add_catalog_line("o-1", "l-1", "c-1", 1)
    with database.session() as session:
        root, version = repositories(session)["ordering"].get("o-1")
        assert root.to_state()["lines"][0]["amount"] == "10.00"
        assert version == 2


def test_order_rejects_mixed_sellers() -> None:
    """Корень Ordering владеет правилом одного продавца."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("a", "A", "A", "seller-a", "10.00", "RUB")
    catalog.create_item("b", "B", "B", "seller-b", "10.00", "RUB")
    ordering.create_order("o", "buyer", "RUB")
    ordering.add_catalog_line("o", "l-a", "a", 1)
    with pytest.raises(DomainRuleViolation, match="одному продавцу"):
        ordering.add_catalog_line("o", "l-b", "b", 1)


def test_http_adapter_has_context_resources() -> None:
    """REST-путь Catalog передаёт DTO прикладной службе."""
    client = TestClient(create_app("sqlite+pysqlite:///:memory:"))
    response = client.post("/catalog/items", json={"item_id": "c", "sku": "BOOK", "title": "Книга", "seller_id": "seller", "amount": "10", "currency": "RUB"})
    assert response.status_code == 200
    assert response.json()["sku"] == "BOOK"


def test_repository_rejects_stale_version_with_conditional_update() -> None:
    """Вторая запись старой версии не перезаписывает уже сохранённый заказ."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    ordering.create_order("o-1", "buyer", "RUB")
    with database.session() as first_session:
        first_root, first_version = repositories(first_session)["ordering"].get("o-1")
    with database.session() as second_session:
        repository = repositories(second_session)["ordering"]
        second_root, second_version = repository.get("o-1")
        second_root.set_shipping_address("Архангельск, 1")
        repository.save(second_root, second_version)
        second_session.commit()
    first_root.set_shipping_address("Мурманск, 2")
    with database.session() as stale_session:
        with pytest.raises(ConcurrencyConflict):
            repositories(stale_session)["ordering"].save(first_root, first_version)


def test_full_synchronous_path_uses_scalar_contracts_and_services() -> None:
    """Контракты и планы связывают пять контекстов без передачи чужих корней."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    inventory = InventoryApplicationService(database)
    payment = PaymentApplicationService(database)
    shipping = ShippingApplicationService(database, ordering)
    catalog.create_item("book", "BOOK", "Книга", "seller", "10.00", "RUB")
    ordering.create_order("order", "buyer", "RUB")
    ordering.add_catalog_line("order", "line", "book", 3)
    ordering.place("order", "Архангельск, 1")
    inventory.create_stock_item("stock-small", "warehouse-a", "BOOK", 2)
    inventory.create_stock_item("stock-large", "warehouse-b", "BOOK", 3)
    reserve = inventory.reserve_by_sku("reservation", "order", "BOOK", 3)
    payment.authorize("payment", "order", "30.00", "RUB")
    shipment = shipping.create_for_order("shipment", "order")
    assert reserve["id"] == "stock-large"
    assert shipment["order_id"] == "order"
    assert shipping.dispatch_ready(["shipment"]) == ["shipment"]


def test_inherited_stage_01_order_story_keeps_its_subject_guarantee() -> None:
    """Путь минимального заказа сохранён через адаптацию к каталожному снимку."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("book", "BOOK", "Книга", "seller", "10.00", "RUB")
    ordering.create_order("order", "buyer", "RUB")
    ordering.add_catalog_line("order", "line", "book", 2)
    placed = ordering.place("order", "Архангельск, 1")
    assert placed["status"] == "PLACED"
    assert placed["lines"][0]["amount"] == "10.00"
