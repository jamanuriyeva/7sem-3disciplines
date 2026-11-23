"""Опциональная интеграционная проверка RabbitMQ через Docker Compose."""

from __future__ import annotations

import os
import socket
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from north_marketplace.application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService
from north_marketplace.api import create_app
from north_marketplace.infrastructure import Database, InboxReceiptRow, OutboxRow
from north_marketplace.rabbit_fulfillment import RabbitFulfillmentApplicationService
from north_marketplace.rabbitmq import NotificationMessage, OutboxRelay, RabbitMqConsumer, RabbitMqPublisher, TransactionalOutbox, default_rabbit_url


RABBIT_URL = os.environ.get("NORTH_RABBITMQ_URL", default_rabbit_url())


def _rabbitmq_is_available() -> bool:
    """Проверяет доступность стандартного локального порта без запуска Docker.

    Compose-набор использует тот же URL, что CLI и REST. При отсутствии уже
    запущенного брокера эти интеграционные проверки остаются opt-in.
    """
    try:
        with socket.create_connection(("127.0.0.1", 5672), timeout=0.2):
            return True
    except OSError:
        return False


RABBIT_AVAILABLE = _rabbitmq_is_available()
REQUIRE_RABBIT = os.environ.get("NORTH_REQUIRE_RABBITMQ") == "1"

if REQUIRE_RABBIT and not RABBIT_AVAILABLE:
    raise RuntimeError(
        "Строгая RabbitMQ-проверка запрошена, но broker недоступен на localhost:5672."
    )


def _delete_test_topology(*consumers: RabbitMqConsumer) -> None:
    """Удаляет временные очереди Compose-проверки до закрытия соединений."""
    for consumer in consumers:
        for name in consumer.topology_queue_names():
            try:
                consumer._channel.queue_delete(name)
            except Exception:
                pass


def _wait_for_failed_delivery(consumer: RabbitMqConsumer) -> None:
    """Ждёт одну доставку, обработчик которой предсказуемо завершится ошибкой."""
    deadline = time.monotonic() + 3
    while True:
        try:
            received = consumer.consume_one()
        except RuntimeError:
            return
        if received:
            raise AssertionError("Отказной consumer неожиданно принял сообщение.")
        assert time.monotonic() < deadline
        time.sleep(0.03)


def _wait_for_queue_message(consumer: RabbitMqConsumer, queue: str):
    """Возвращает одну готовую RabbitMQ-доставку после ограниченного ожидания."""
    deadline = time.monotonic() + 3
    while True:
        method, properties, body = consumer._channel.basic_get(queue, auto_ack=False)
        if method is not None:
            return method, properties, body
        assert time.monotonic() < deadline
        time.sleep(0.03)


@pytest.mark.skipif(not RABBIT_AVAILABLE, reason="Запустите Docker Compose с RabbitMQ на localhost:5672.")
def test_rabbitmq_outbox_inbox_and_process_roundtrip(tmp_path) -> None:
    """Confirm, inbox и ручной ack проводят реальный заказ через RabbitMQ."""
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'north.db'}")
    catalog = CatalogApplicationService(database)
    inventory = InventoryApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("catalog-book", "BOOK", "Книга", "seller", "10.00", "RUB")
    inventory.create_stock_item("stock-book", "warehouse", "BOOK", 1)
    ordering.create_order("order-1", "buyer", "RUB")
    ordering.add_catalog_line("order-1", "line-1", "catalog-book", 1)
    ordering.place("order-1", "Архангельск, 1")
    service = RabbitFulfillmentApplicationService(database, database)
    publisher = RabbitMqPublisher(RABBIT_URL)
    queue = f"north.integration.{uuid.uuid4().hex}"
    consumer = RabbitMqConsumer(
        RABBIT_URL,
        queue,
        service.subscriber_name,
        database,
        service.accept_message,
        service.after_message_commit,
        event_binding_keys=service.event_binding_keys,
    )
    relay = OutboxRelay(TransactionalOutbox(database), publisher)
    try:
        for _ in range(20):
            relay.relay(10)
            consumer.consume_one()
            state = service.state("order-1")
            if state is not None and state["stage"] == "COMPLETED":
                break
        assert ordering.get("order-1")["status"] == "FULFILLED"
        assert service.state("order-1")["stage"] == "COMPLETED"
    finally:
        _delete_test_topology(consumer)
        consumer.close()
        publisher.close()


@pytest.mark.skipif(not RABBIT_AVAILABLE, reason="Запустите Docker Compose с RabbitMQ на localhost:5672.")
def test_rabbitmq_retry_reaches_dead_letter_queue_after_limit(tmp_path) -> None:
    """Неисправимый consumer подтверждённо проходит retry и попадает в DLQ."""
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'retry.db'}")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("catalog-book", "BOOK", "Книга", "seller", "10.00", "RUB")
    ordering.create_order("order-retry", "buyer", "RUB")
    ordering.add_catalog_line("order-retry", "line-retry", "catalog-book", 1)
    ordering.place("order-retry", "Архангельск, 1")

    def always_fail(message, uow) -> None:
        """Имитирует отказ локальной транзакции до создания inbox receipt."""
        raise RuntimeError("Временная ошибка подписчика")

    publisher = RabbitMqPublisher(RABBIT_URL)
    queue = f"north.retry.{uuid.uuid4().hex}"
    consumer = RabbitMqConsumer(RABBIT_URL, queue, "failing-consumer", database, always_fail, max_attempts=3)
    observer = RabbitMqConsumer(
        RABBIT_URL,
        f"north.retry-observer.{uuid.uuid4().hex}",
        "retry-observer",
        database,
        always_fail,
        event_binding_keys=("inventory.InventoryReserved",),
    )
    relay = OutboxRelay(TransactionalOutbox(database), publisher)
    try:
        with database.unit_of_work() as uow:
            row = uow.session.execute(select(OutboxRow)).scalar_one()
            expected_message_id = row.message_id
            expected_position = row.global_position
            expected_routing_key = row.routing_key
        assert relay.relay(1) == 1
        _wait_for_failed_delivery(consumer)
        retry_count = consumer._channel.queue_declare(queue=consumer.topology_queue_names()[1], passive=True)
        observer_retry_count = observer._channel.queue_declare(queue=observer.topology_queue_names()[1], passive=True)
        assert retry_count.method.message_count == 1
        assert observer_retry_count.method.message_count == 0
        _wait_for_failed_delivery(consumer)
        _wait_for_failed_delivery(consumer)
        _, properties, body = _wait_for_queue_message(consumer, consumer.topology_queue_names()[2])
        message = NotificationMessage.from_body(body)
        assert properties.headers["x-retry-count"] == 3
        assert message.message_id == expected_message_id
        assert message.global_position == expected_position
        assert message.routing_key == expected_routing_key
    finally:
        _delete_test_topology(consumer, observer)
        consumer.close()
        observer.close()
        publisher.close()


@pytest.mark.skipif(not RABBIT_AVAILABLE, reason="Запустите Docker Compose с RabbitMQ на localhost:5672.")
def test_confirmed_message_is_safe_after_relay_crash_before_outbox_mark(tmp_path) -> None:
    """Повтор после confirm не дублирует локальную реакцию благодаря inbox.

    Первый relay получает confirm, но намеренно не вызывает ``mark_published``:
    это модель остановки процесса после публикации и до смены состояния outbox.
    Просроченный lease позволяет второму relay опубликовать тот же контракт
    повторно. Consumer принимает обе доставки, однако создаёт ровно один inbox
    receipt и ровно одну локальную реакцию process manager.
    """
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'crash-after-confirm.db'}")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("catalog-book", "BOOK", "Книга", "seller", "10.00", "RUB")
    ordering.create_order("order-confirm", "buyer", "RUB")
    ordering.add_catalog_line("order-confirm", "line-confirm", "catalog-book", 1)
    ordering.place("order-confirm", "Архангельск, 1")

    queue = f"north.confirm-crash.{uuid.uuid4().hex}"
    publisher = RabbitMqPublisher(RABBIT_URL)
    service = RabbitFulfillmentApplicationService(database, database)
    consumer = RabbitMqConsumer(
        RABBIT_URL,
        queue,
        service.subscriber_name,
        database,
        service.accept_message,
        service.after_message_commit,
        event_binding_keys=service.event_binding_keys,
    )
    outbox = TransactionalOutbox(database)
    try:
        claimed = outbox.claim_pending(1, lease_seconds=-1)[0]
        publisher.publish(claimed.routing_key, claimed.body, claimed.message_id)
        # Здесь первый relay аварийно завершился: lease уже истёк, но строка
        # осталась LEASED и будет повторно опубликована другим relay.
        assert OutboxRelay(outbox, publisher).relay(1) == 1
        assert consumer.consume_one()
        assert consumer.consume_one()
        with database.unit_of_work() as uow:
            receipts = uow.session.execute(select(InboxReceiptRow).where(InboxReceiptRow.consumer_name == service.subscriber_name)).scalars().all()
            assert len(receipts) == 1
            assert uow.processes().get("order-confirm") is not None
    finally:
        _delete_test_topology(consumer)
        consumer.close()
        publisher.close()


@pytest.mark.skipif(not RABBIT_AVAILABLE, reason="Запустите Docker Compose с RabbitMQ на localhost:5672.")
def test_rabbitmq_rest_transport_completes_async_order(tmp_path) -> None:
    """REST фиксирует команду асинхронно, а transport завершает процесс отдельно."""
    queue = f"north.http.{uuid.uuid4().hex}"
    database_path = tmp_path / "http.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    client = TestClient(create_app(database_url, RABBIT_URL, queue))
    assert client.post("/catalog/items", json={"item_id": "catalog-book", "sku": "BOOK", "title": "Книга", "seller_id": "seller", "amount": "10.00"}).status_code == 200
    assert client.post("/inventory/items", json={"item_id": "stock-book", "warehouse_id": "warehouse", "sku": "BOOK", "quantity": 1}).status_code == 200
    assert client.post("/orders", json={"order_id": "order-http", "buyer_id": "buyer"}).status_code == 200
    assert client.post("/orders/order-http/lines", json={"line_id": "line-http", "catalog_item_id": "catalog-book", "quantity": 1}).status_code == 200
    assert client.post("/orders/order-http/place", json={"address": "Архангельск, 1"}).status_code == 202
    database = Database(database_url)
    service = RabbitFulfillmentApplicationService(database, database)
    cleanup_consumer = RabbitMqConsumer(
        RABBIT_URL,
        queue,
        service.subscriber_name,
        database,
        service.accept_message,
        service.after_message_commit,
        event_binding_keys=service.event_binding_keys,
    )
    try:
        for _ in range(20):
            client.post("/transport/relay", params={"limit": 10})
            client.post("/transport/consume", params={"limit": 10})
            client.post("/processing/resume", params={"limit": 10})
            process = client.get("/fulfillment/order-http")
            if process.status_code == 200 and process.json()["stage"] == "COMPLETED":
                break
        assert client.get("/orders/order-http").json()["status"] == "FULFILLED"
        assert client.get("/fulfillment/order-http").json()["stage"] == "COMPLETED"
    finally:
        _delete_test_topology(cleanup_consumer)
        cleanup_consumer.close()
