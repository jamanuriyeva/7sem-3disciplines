"""Регрессия ручного HTTP-маршрута, сохранённого от второй стадии."""

from fastapi.testclient import TestClient

from north_marketplace.api import create_app


def test_manual_http_route_keeps_each_aggregate_command_local() -> None:
    """REST сохраняет заказ, резерв, платёж и отправление отдельными командами."""
    client = TestClient(create_app("sqlite+pysqlite:///:memory:"))
    assert client.post("/catalog/items", json={"item_id": "book", "sku": "BOOK", "title": "Книга", "seller_id": "seller", "amount": "10.00"}).status_code == 200
    assert client.post("/inventory/items", json={"item_id": "stock", "warehouse_id": "warehouse", "sku": "BOOK", "quantity": 2}).status_code == 200
    assert client.post("/orders", json={"order_id": "order", "buyer_id": "buyer"}).status_code == 200
    assert client.post("/orders/order/lines", json={"line_id": "line", "catalog_item_id": "book", "quantity": 1}).status_code == 200
    assert client.post("/orders/order/place", json={"address": "Архангельск, 1"}).status_code == 202
    assert client.post("/inventory/reservations", json={"reservation_id": "reservation", "order_id": "order", "sku": "BOOK", "quantity": 1}).status_code == 200
    assert client.post("/payments/payment/authorize", params={"order_id": "order", "amount": "10.00"}).status_code == 200
    assert client.post("/payments/payment/capture").status_code == 200
    assert client.post("/shipments", json={"shipment_id": "shipment", "order_id": "order"}).status_code == 200
    assert client.post("/shipments/shipment/dispatch").json() == ["shipment"]


def test_http_processing_route_separates_notification_from_process_command() -> None:
    """HTTP-цикл сохраняет факт до выполнения следующей команды процесса.

    Сначала ``dispatch`` принимает ``OrderPlaced`` и фиксирует work-item.
    Только отдельный вызов ``resume`` может авторизовать платёж. Повторение
    пары операций проводит весь процесс без общей транзакции контекстов.
    """
    client = TestClient(create_app("sqlite+pysqlite:///:memory:"))
    assert client.post("/catalog/items", json={"item_id": "book", "sku": "BOOK", "title": "Книга", "seller_id": "seller", "amount": "10.00"}).status_code == 200
    assert client.post("/inventory/items", json={"item_id": "stock", "warehouse_id": "warehouse", "sku": "BOOK", "quantity": 1}).status_code == 200
    assert client.post("/orders", json={"order_id": "order-process", "buyer_id": "buyer"}).status_code == 200
    assert client.post("/orders/order-process/lines", json={"line_id": "line", "catalog_item_id": "book", "quantity": 1}).status_code == 200
    assert client.post("/orders/order-process/place", json={"address": "Архангельск, 1"}).status_code == 202

    assert client.post("/processing/dispatch", params={"limit": 1}).json() == {"delivered": 1}
    assert client.post("/processing/resume", params={"limit": 1}).json() == {"executed": 1}
    for _ in range(12):
        client.post("/processing/dispatch", params={"limit": 10})
        client.post("/processing/resume", params={"limit": 10})
        if client.get("/orders/order-process").json()["status"] == "FULFILLED":
            break
    assert client.get("/orders/order-process").json()["status"] == "FULFILLED"
