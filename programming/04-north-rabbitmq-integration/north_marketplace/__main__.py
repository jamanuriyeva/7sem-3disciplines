"""Командный вход демонстрации RabbitMQ-доставки уведомлений."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService
from .fulfillment_application import FulfillmentApplicationService
from .infrastructure import Database
from .rabbit_fulfillment import RabbitFulfillmentApplicationService
from .rabbitmq import OutboxRelay, RabbitMqConsumer, RabbitMqPublisher, TransactionalOutbox, default_rabbit_url


def _database(path: str, reset: bool) -> Database:
    """Создаёт SQLite-базу; ``reset`` удаляет только явно названный файл.

    Args:
        path: Путь к локальному файлу SQLite.
        reset: Признак создания чистого демонстрационного запуска.

    Returns:
        Фабрика локальных транзакций приложения.
    """
    file = Path(path)
    if reset and file.exists():
        file.unlink()
    return Database(f"sqlite+pysqlite:///{file}")


def _prepare(database: Database, scenario: str) -> None:
    """Фиксирует один воспроизводимый успешный либо отказной заказ.

    Args:
        database: База текущего демонстрационного запуска.
        scenario: success оставляет достаточный остаток, failure создаёт
            нехватку для второй строки.
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
    ordering.place("order-001", "Архангельск, Поморская, 18")


def _relay(database: Database, rabbit_url: str, limit: int) -> int:
    """Публикует сохранённые контракты после подтверждения RabbitMQ.

    Args:
        database: База с transactional outbox.
        rabbit_url: Адрес RabbitMQ.
        limit: Верхняя граница количества публикаций.

    Returns:
        Число подтверждённо опубликованных сообщений.
    """
    publisher = RabbitMqPublisher(rabbit_url)
    try:
        return OutboxRelay(TransactionalOutbox(database), publisher).relay(limit)
    finally:
        publisher.close()


def _consume(database: Database, rabbit_url: str, queue: str, limit: int) -> int:
    """Принимает ограниченное число сообщений и только затем подтверждает их.

    Args:
        database: База consumer inbox и процесса.
        rabbit_url: Адрес RabbitMQ.
        queue: Главная durable-очередь демонстрационного подписчика.
        limit: Верхняя граница получаемых сообщений.

    Returns:
        Число обработанных доставок.
    """
    service = RabbitFulfillmentApplicationService(database, database)
    consumer = RabbitMqConsumer(
        rabbit_url,
        queue,
        service.subscriber_name,
        database,
        service.accept_message,
        service.after_message_commit,
        event_binding_keys=service.event_binding_keys,
    )
    consumed = 0
    try:
        for _ in range(limit):
            if not consumer.consume_one():
                break
            consumed += 1
    finally:
        consumer.close()
    return consumed


def _reset_transport(database: Database, rabbit_url: str, queue: str) -> None:
    """Очищает только транспортные очереди выбранного demo-подписчика.

    Args:
        database: Новая база демонстрационного запуска.
        rabbit_url: Адрес RabbitMQ.
        queue: Главная очередь, её retry-очередь и DLQ.

    Note:
        Сброс нужен только вместе с ``--reset``: новая SQLite-база не должна
        получать доставки от предыдущего демонстрационного состояния.
    """
    service = RabbitFulfillmentApplicationService(database, database)
    consumer = RabbitMqConsumer(
        rabbit_url,
        queue,
        service.subscriber_name,
        database,
        service.accept_message,
        service.after_message_commit,
        event_binding_keys=service.event_binding_keys,
    )
    try:
        consumer.purge_owned_queues()
    finally:
        consumer.close()


def main() -> None:
    """Запускает основной RabbitMQ-путь или совместимый polling-режим.

    Note:
        Параметр transport=polling сохраняет диагностический путь стадии 03,
        включая scenario и max-notifications. Без него основной путь
        использует RabbitMQ.
    """
    parser = argparse.ArgumentParser(description="Север: RabbitMQ-доставка доменных уведомлений")
    parser.add_argument("command", nargs="?", choices=["prepare", "relay", "consume", "resume", "state", "demo"], default="demo")
    parser.add_argument("--database", default="north-stage-04.db")
    parser.add_argument("--rabbit-url", default=default_rabbit_url())
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--scenario", choices=["success", "failure"], default="success")
    parser.add_argument("--max-notifications", type=int, default=1)
    parser.add_argument("--transport", choices=["rabbit", "polling"], default="rabbit")
    parser.add_argument("--queue", default="north.fulfillment")
    parser.add_argument("--reset", action="store_true")
    arguments = parser.parse_args()
    database = _database(arguments.database, arguments.reset)

    if arguments.reset and arguments.transport == "rabbit" and arguments.command in {"prepare", "demo"}:
        _reset_transport(database, arguments.rabbit_url, arguments.queue)

    if arguments.command == "prepare":
        _prepare(database, arguments.scenario)
        ordering = OrderingApplicationService(database, CatalogApplicationService(database))
        print(json.dumps({"order": ordering.get("order-001")}, ensure_ascii=False))
        return
    if arguments.command == "relay":
        print(json.dumps({"published": _relay(database, arguments.rabbit_url, arguments.limit)}, ensure_ascii=False))
        return
    if arguments.command == "consume":
        print(json.dumps({"consumed": _consume(database, arguments.rabbit_url, arguments.queue, arguments.limit)}, ensure_ascii=False))
        return
    if arguments.command == "resume":
        print(json.dumps({"executed": FulfillmentApplicationService(database, database).dispatch_commands(arguments.limit)}, ensure_ascii=False))
        return

    if arguments.command == "state":
        ordering = OrderingApplicationService(database, CatalogApplicationService(database))
        process = FulfillmentApplicationService(database, database)
        print(json.dumps({"order": ordering.get("order-001"), "process": process.state("order-001"), "outbox": TransactionalOutbox(database).state_counts()}, ensure_ascii=False))
        return

    _prepare(database, arguments.scenario)
    ordering = OrderingApplicationService(database, CatalogApplicationService(database))
    if arguments.transport == "polling":
        process = FulfillmentApplicationService(database, database)
        while process.resume(arguments.max_notifications):
            pass
        print(json.dumps({"order": ordering.get("order-001"), "process": process.state("order-001")}, ensure_ascii=False))
        return

    for _ in range(arguments.limit):
        published = _relay(database, arguments.rabbit_url, arguments.limit)
        consumed = _consume(database, arguments.rabbit_url, arguments.queue, arguments.limit)
        if published == 0 and consumed == 0:
            break
        process_state = FulfillmentApplicationService(database, database).state("order-001")
        if process_state is not None and process_state["stage"] in {"COMPLETED", "REJECTED"}:
            break
    process = FulfillmentApplicationService(database, database)
    print(json.dumps({"order": ordering.get("order-001"), "process": process.state("order-001")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
