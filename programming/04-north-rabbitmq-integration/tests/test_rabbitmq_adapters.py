"""Проверки outbox, inbox и relay без запуска внешнего RabbitMQ."""

from sqlalchemy import select

from north_marketplace.application import CatalogApplicationService, OrderingApplicationService
from north_marketplace.infrastructure import Database, InboxReceiptRow, OutboxRow
from north_marketplace.rabbit_fulfillment import RabbitFulfillmentApplicationService
from north_marketplace.rabbitmq import InMemoryPublisher, NotificationMessage, OutboxRelay, TransactionalOutbox


def _placed_order(database: Database) -> None:
    """Создаёт размещённый заказ, который должен породить outbox-контракт."""
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("catalog-1", "BOOK", "Книга", "seller-1", "10.00", "RUB")
    ordering.create_order("order-1", "buyer-1", "RUB")
    ordering.add_catalog_line("order-1", "line-1", "catalog-1", 1)
    ordering.place("order-1", "Архангельск, 1")


def test_outbox_is_saved_with_order_event_and_relay_waits_for_confirm() -> None:
    """Размещение создаёт event и outbox одной транзакцией, relay публикует раз."""
    database = Database("sqlite+pysqlite:///:memory:")
    _placed_order(database)
    with database.unit_of_work() as uow:
        rows = uow.session.execute(select(OutboxRow)).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "PENDING"
        expected_position = rows[0].global_position
    publisher = InMemoryPublisher()
    assert OutboxRelay(TransactionalOutbox(database), publisher).relay() == 1
    assert len(publisher.messages) == 1
    message = NotificationMessage.from_body(publisher.messages[0][1])
    assert message.global_position == expected_position
    assert message.notification().position == expected_position
    assert message.routing_key == publisher.messages[0][0]
    assert TransactionalOutbox(database).claim_pending() == []


def test_failed_publish_releases_lease_for_later_attempt() -> None:
    """Ошибка publisher не теряет контракт и возвращает запись в pending."""
    class FailingPublisher:
        """Имитирует отсутствие broker confirm."""

        def publish(self, routing_key: str, body: bytes, message_id: str) -> None:
            """Всегда отклоняет публикацию."""
            raise RuntimeError("confirm не получен")

    database = Database("sqlite+pysqlite:///:memory:")
    _placed_order(database)
    assert OutboxRelay(TransactionalOutbox(database), FailingPublisher()).relay() == 0
    assert len(TransactionalOutbox(database).claim_pending()) == 1


def test_lease_token_prevents_second_relay_from_finishing_foreign_claim() -> None:
    """Активный lease принадлежит одному relay и не завершается чужим токеном."""
    database = Database("sqlite+pysqlite:///:memory:")
    _placed_order(database)
    outbox = TransactionalOutbox(database)
    claimed = outbox.claim_pending()[0]
    assert outbox.claim_pending() == []
    assert not outbox.mark_published(claimed.message_id, "foreign-token")
    with database.unit_of_work() as uow:
        row = uow.session.get(OutboxRow, claimed.message_id)
        assert row.status == "LEASED"
    assert outbox.mark_published(claimed.message_id, claimed.lease_token)
    with database.unit_of_work() as uow:
        row = uow.session.get(OutboxRow, claimed.message_id)
        assert row.status == "PUBLISHED"


def test_lease_token_prevents_foreign_relay_from_releasing_claim() -> None:
    """Освобождение, как и публикация, возвращает различимый результат lease."""
    database = Database("sqlite+pysqlite:///:memory:")
    _placed_order(database)
    outbox = TransactionalOutbox(database)
    claimed = outbox.claim_pending()[0]
    assert not outbox.release(claimed.message_id, "foreign-token", "чужая ошибка")
    assert outbox.release(claimed.message_id, claimed.lease_token, "confirm не получен")
    assert len(outbox.claim_pending()) == 1


def test_consumer_transaction_can_save_process_and_inbox_receipt_together() -> None:
    """Локальная реакция процесса и inbox-receipt откатываются либо фиксируются вместе."""
    database = Database("sqlite+pysqlite:///:memory:")
    _placed_order(database)
    outbox = TransactionalOutbox(database)
    claimed = outbox.claim_pending()[0]
    message = NotificationMessage.from_body(claimed.body)
    service = RabbitFulfillmentApplicationService(database, database)
    with database.unit_of_work() as uow:
        service.accept_message(message, uow)
        uow.mark_inbox_received(service.subscriber_name, message.message_id)
    with database.unit_of_work() as uow:
        assert uow.has_inbox_receipt(service.subscriber_name, message.message_id)
        assert uow.processes().get("order-1") is not None
        rows = uow.session.execute(select(InboxReceiptRow)).scalars().all()
        assert len(rows) == 1


def test_consumer_failure_rolls_back_inbox_and_process_reaction() -> None:
    """Ошибка до commit не оставляет inbox receipt и не поглощает повтор."""
    database = Database("sqlite+pysqlite:///:memory:")
    _placed_order(database)
    claimed = TransactionalOutbox(database).claim_pending()[0]
    message = NotificationMessage.from_body(claimed.body)
    service = RabbitFulfillmentApplicationService(database, database)
    try:
        with database.unit_of_work() as uow:
            service.accept_message(message, uow)
            uow.mark_inbox_received(service.subscriber_name, message.message_id)
            raise RuntimeError("Имитация аварии до commit")
    except RuntimeError:
        pass
    with database.unit_of_work() as uow:
        assert not uow.has_inbox_receipt(service.subscriber_name, message.message_id)
        assert uow.processes().get("order-1") is None
