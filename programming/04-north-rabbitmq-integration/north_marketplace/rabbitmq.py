"""Инфраструктурные адаптеры RabbitMQ, outbox, inbox и ручного подтверждения.

Модуль не импортируется предметной моделью: доменные факты уже сохранены
локальной транзакцией до того, как relay пытается их опубликовать.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Callable

import pika
from sqlalchemy import or_, select, update

from .events import Notification
from .infrastructure import Database, OutboxRow, UnitOfWork


def default_rabbit_url() -> str:
    """Возвращает URL учебного RabbitMQ с возможностью внешней настройки.

    Compose-окружение этой стадии создаёт пользователя ``north``. Переменная
    ``NORTH_RABBITMQ_URL`` нужна для внешнего broker без изменения кода.
    """
    return os.environ.get("NORTH_RABBITMQ_URL", "amqp://north:north@localhost:5672/%2F")


class NotificationMessage:
    """Версионированный скалярный контракт, производный от уведомления.

    Note:
        Контракт не является доменным событием и не передаёт агрегат. Он
        развивает плоское ``Notification`` polling-стадии добавлением версии,
        routing key и идентификатора доставки, нужных только транспорту.
    """

    def __init__(self, message_id: str, schema_version: int, event_id: str, global_position: int, context_name: str, event_type: str, aggregate_id: str, correlation_id: str, causation_id: str | None, payload: dict, routing_key: str) -> None:
        """Сохраняет поля переносимого сообщения без объекта агрегата."""
        self.message_id = message_id
        self.schema_version = schema_version
        self.event_id = event_id
        self.global_position = global_position
        self.context_name = context_name
        self.event_type = event_type
        self.aggregate_id = aggregate_id
        self.correlation_id = correlation_id
        self.causation_id = causation_id
        self.payload = dict(payload)
        self.routing_key = routing_key

    @classmethod
    def from_row(cls, row: OutboxRow) -> "NotificationMessage":
        """Восстанавливает контракт из уже сохранённой outbox-записи."""
        data = json.loads(row.body_json)
        return cls(data["message_id"], data["schema_version"], data["event_id"], data["global_position"], data["context_name"], data["event_type"], data["aggregate_id"], data["correlation_id"], data["causation_id"], data["payload"], row.routing_key)

    @classmethod
    def from_body(cls, body: bytes) -> "NotificationMessage":
        """Проверяет и восстанавливает контракт из тела RabbitMQ-сообщения.

        Ключ доставки RabbitMQ намеренно не передаётся параметром. После
        задержанного повтора broker направляет то же тело техническим ключом
        возврата, тогда как ``routing_key`` контракта остаётся исходным ключом
        предметного факта.

        Raises:
            ValueError: Версия контракта не поддерживается либо отсутствуют
                обязательные поля.
        """
        data = json.loads(body.decode("utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("Версия интеграционного контракта не поддерживается.")
        required = {"message_id", "event_id", "global_position", "context_name", "event_type", "aggregate_id", "correlation_id", "routing_key", "payload"}
        if not required.issubset(data):
            raise ValueError("Интеграционный контракт не содержит обязательных полей.")
        return cls(data["message_id"], data["schema_version"], data["event_id"], int(data["global_position"]), data["context_name"], data["event_type"], data["aggregate_id"], data["correlation_id"], data.get("causation_id"), data["payload"], data["routing_key"])

    def body(self) -> bytes:
        """Сериализует стабильный контракт без Python-объектов."""
        return json.dumps({"message_id": self.message_id, "schema_version": self.schema_version, "event_id": self.event_id, "global_position": self.global_position, "context_name": self.context_name, "event_type": self.event_type, "aggregate_id": self.aggregate_id, "correlation_id": self.correlation_id, "causation_id": self.causation_id, "routing_key": self.routing_key, "payload": self.payload}, ensure_ascii=False).encode("utf-8")

    def notification(self) -> Notification:
        """Создаёт локальное уведомление для process manager.

        Глобальная позиция принадлежит журналу производителя, поэтому защитой
        от повторов межконтекстного сообщения служит message_id из inbox.
        """
        return Notification(self.global_position, self.event_id, self.context_name, self.event_type, self.aggregate_id, self.correlation_id, self.causation_id, self.payload)


class ClaimedOutboxMessage:
    """Сообщение, временно захваченное одним relay."""

    def __init__(self, message_id: str, routing_key: str, body: bytes, lease_token: str) -> None:
        """Фиксирует данные захваченной записи для одного relay.

        Args:
            message_id: Идентификатор строки исходящей очереди.
            routing_key: Ключ маршрутизации стабильного контракта.
            body: Сериализованное тело сообщения.
            lease_token: Токен временного права relay на смену состояния.
        """
        self.message_id = message_id
        self.routing_key = routing_key
        self.body = body
        self.lease_token = lease_token


class TransactionalOutbox:
    """Работает с outbox-строками общей базы агрегатов и журнала.

    Note:
        Outbox не меняет правило создания факта агрегатом. Он обеспечивает,
        что после commit факт имеет устойчивый транспортный контракт, а
        повтор после publisher confirm остаётся допустимым.
    """

    def __init__(self, database: Database) -> None:
        """Получает фабрику общих Unit of Work."""
        self._database = database

    def claim_pending(self, limit: int = 100, lease_seconds: int = 30) -> list[ClaimedOutboxMessage]:
        """Условно захватывает pending или просроченные записи одной транзакцией.

        Отдельный токен lease не позволяет relay, потерявшему право на запись,
        отметить опубликованным сообщение, уже заново захваченное другим relay.
        """
        now = datetime.now(UTC)
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._database.unit_of_work() as uow:
            rows = uow.session.execute(
                select(OutboxRow).where(
                    or_(OutboxRow.status == "PENDING", (OutboxRow.status == "LEASED") & (OutboxRow.leased_until < now.isoformat()))
            ).order_by(OutboxRow.message_id).limit(limit)
            ).scalars().all()
            claimed: list[ClaimedOutboxMessage] = []
            for row in rows:
                token = str(uuid.uuid4())
                result = uow.session.execute(
                    update(OutboxRow)
                    .where(OutboxRow.message_id == row.message_id, or_(OutboxRow.status == "PENDING", (OutboxRow.status == "LEASED") & (OutboxRow.leased_until < now.isoformat())))
                    .values(status="LEASED", lease_token=token, leased_until=lease, attempts=row.attempts + 1)
                )
                if result.rowcount == 1:
                    claimed.append(ClaimedOutboxMessage(row.message_id, row.routing_key, NotificationMessage.from_row(row).body(), token))
            return claimed

    def mark_published(self, message_id: str, lease_token: str) -> bool:
        """Фиксирует публикацию только после confirm и для владельца lease.

        Returns:
            True, если relay всё ещё владеет захватом и запись переведена
            в состояние PUBLISHED; иначе False.
        """
        with self._database.unit_of_work() as uow:
            result = uow.session.execute(
                update(OutboxRow)
                .where(OutboxRow.message_id == message_id, OutboxRow.status == "LEASED", OutboxRow.lease_token == lease_token)
                .values(status="PUBLISHED", lease_token=None, leased_until=None, last_error=None)
            )
            return result.rowcount == 1

    def release(self, message_id: str, lease_token: str, reason: str) -> bool:
        """Возвращает неподтверждённую публикацию в pending для повторения.

        Returns:
            True, если запись принадлежала данному relay; иначе False.
        """
        with self._database.unit_of_work() as uow:
            result = uow.session.execute(
                update(OutboxRow)
                .where(OutboxRow.message_id == message_id, OutboxRow.status == "LEASED", OutboxRow.lease_token == lease_token)
                .values(status="PENDING", lease_token=None, leased_until=None, last_error=reason)
            )
            return result.rowcount == 1

    def state_counts(self) -> dict[str, int]:
        """Возвращает наблюдаемое число сообщений в каждом состоянии outbox."""
        with self._database.unit_of_work() as uow:
            counts: dict[str, int] = {"PENDING": 0, "LEASED": 0, "PUBLISHED": 0}
            for status in uow.session.execute(select(OutboxRow.status)).scalars().all():
                counts[status] = counts.get(status, 0) + 1
            return counts


class Publisher:
    """Инфраструктурный порт публикации persistent-сообщения."""

    def publish(self, routing_key: str, body: bytes, message_id: str) -> None:
        """Публикует контракт либо сообщает об отсутствии подтверждения."""
        raise NotImplementedError


class InMemoryPublisher(Publisher):
    """Тестовый publisher, считающий каждую запись подтверждённой."""

    def __init__(self) -> None:
        """Создаёт наблюдаемый буфер контрактов."""
        self.messages: list[tuple[str, bytes, str]] = []

    def publish(self, routing_key: str, body: bytes, message_id: str) -> None:
        """Сохраняет подтверждённую публикацию в памяти."""
        self.messages.append((routing_key, body, message_id))


class RabbitMqPublisher(Publisher):
    """RabbitMQ publisher с mandatory routing и publisher confirms."""

    def __init__(self, url: str, exchange: str = "north.events") -> None:
        """Открывает подключение и объявляет durable exchange."""
        self._connection = pika.BlockingConnection(pika.URLParameters(url))
        self._channel = self._connection.channel()
        self._exchange = exchange
        self._channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        self._channel.confirm_delivery()

    def publish(self, routing_key: str, body: bytes, message_id: str) -> None:
        """Публикует persistent-контракт и ждёт broker confirm."""
        confirmed = self._channel.basic_publish(self._exchange, routing_key, body, properties=pika.BasicProperties(message_id=message_id, delivery_mode=2, content_type="application/json"), mandatory=True)
        if confirmed is False:
            raise RuntimeError("RabbitMQ не подтвердил публикацию.")

    def close(self) -> None:
        """Закрывает сетевое подключение publisher."""
        self._connection.close()


class OutboxRelay:
    """Публикует захваченные сообщения и освобождает их при ошибке."""

    def __init__(self, outbox: TransactionalOutbox, publisher: Publisher) -> None:
        """Связывает outbox с конкретным инфраструктурным publisher."""
        self._outbox, self._publisher = outbox, publisher

    def relay(self, limit: int = 100) -> int:
        """Публикует ограниченную порцию pending-строк."""
        sent = 0
        for message in self._outbox.claim_pending(limit):
            try:
                self._publisher.publish(message.routing_key, message.body, message.message_id)
            except Exception as error:
                self._outbox.release(message.message_id, message.lease_token, str(error))
                continue
            if self._outbox.mark_published(message.message_id, message.lease_token):
                sent += 1
        return sent


class RabbitMqConsumer:
    """Потребляет контракт, фиксирует inbox с локальной командой и затем ack.

    Для каждого подписчика создаются собственные retry- и DLQ-маршруты.
    Поэтому задержанный повтор возвращается только в очередь, где исходная
    локальная транзакция завершилась ошибкой, и не становится новым фактом для
    остальных подписчиков.
    """

    _retry_exchange = "north.delivery.retry"
    _retry_return_exchange = "north.delivery.return"
    _dead_letter_exchange = "north.delivery.dead"

    def __init__(self, url: str, queue: str, consumer_name: str, database: Database, handler: Callable[[NotificationMessage, UnitOfWork], None], after_commit: Callable[[NotificationMessage], None] | None = None, exchange: str = "north.events", max_attempts: int = 3, event_binding_keys: tuple[str, ...] = ("#",)) -> None:
        """Настраивает durable topology и обработчик одной локальной модели.

        Args:
            url: URL RabbitMQ.
            queue: Устойчивое имя главной очереди подписчика.
            consumer_name: Имя inbox-потребителя.
            database: Фабрика локальных транзакций consumer.
            handler: Реакция прикладного слоя внутри открытой транзакции.
            after_commit: Запуск устойчивой команды после локального commit и
                manual acknowledgement; сама команда уже сохранена как
                work-item до acknowledgement.
            exchange: Topic exchange исходных предметных уведомлений.
            max_attempts: Число неудачных доставок до собственной DLQ.
            event_binding_keys: Точные интересующие подписчика ключи фактов;
                ``("#",)`` оставлен для изолированных диагностических очередей.

        Raises:
            ValueError: Число попыток или набор подписок некорректны.
        """
        if max_attempts < 1:
            raise ValueError("Число попыток должно быть положительным.")
        if not event_binding_keys:
            raise ValueError("Подписчик должен указать хотя бы один ключ факта.")
        self._connection = pika.BlockingConnection(pika.URLParameters(url))
        self._channel = self._connection.channel()
        self._channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        self._channel.exchange_declare(exchange=self._retry_exchange, exchange_type="direct", durable=True)
        self._channel.exchange_declare(exchange=self._retry_return_exchange, exchange_type="direct", durable=True)
        self._channel.exchange_declare(exchange=self._dead_letter_exchange, exchange_type="direct", durable=True)
        self._queue = queue
        self._retry_queue = f"{queue}.retry.delivery"
        self._dead_letter_queue = f"{queue}.dead.letter"
        self._retry_routing_key = f"retry.{queue}"
        self._dead_letter_routing_key = f"dead.{queue}"
        self._channel.queue_declare(
            queue=self._retry_queue,
            durable=True,
            arguments={
                "x-message-ttl": 250,
                "x-dead-letter-exchange": self._retry_return_exchange,
                "x-dead-letter-routing-key": self._retry_routing_key,
            },
        )
        self._channel.queue_bind(queue=self._retry_queue, exchange=self._retry_exchange, routing_key=self._retry_routing_key)
        self._channel.queue_declare(queue=self._dead_letter_queue, durable=True)
        self._channel.queue_bind(queue=self._dead_letter_queue, exchange=self._dead_letter_exchange, routing_key=self._dead_letter_routing_key)
        self._channel.queue_declare(queue=queue, durable=True)
        for routing_key in event_binding_keys:
            self._channel.queue_bind(queue=queue, exchange=exchange, routing_key=routing_key)
        self._channel.queue_bind(queue=queue, exchange=self._retry_return_exchange, routing_key=self._retry_routing_key)
        self._channel.confirm_delivery()
        self._consumer_name, self._database = consumer_name, database
        self._handler, self._after_commit, self._max_attempts = handler, after_commit, max_attempts

    def consume_one(self) -> bool:
        """Обрабатывает одну доставку; manual ack следует только за commit."""
        method, properties, body = self._channel.basic_get(self._queue, auto_ack=False)
        if method is None:
            return False
        try:
            message = NotificationMessage.from_body(body)
            duplicate = False
            with self._database.unit_of_work() as uow:
                duplicate = uow.has_inbox_receipt(self._consumer_name, message.message_id)
                if not duplicate:
                    self._handler(message, uow)
                    uow.mark_inbox_received(self._consumer_name, message.message_id)
            self._channel.basic_ack(method.delivery_tag)
            if not duplicate and self._after_commit is not None:
                self._after_commit(message)
            return True
        except Exception:
            attempts = int((properties.headers or {}).get("x-retry-count", 0)) + 1
            headers = dict(properties.headers or {})
            headers["x-retry-count"] = attempts
            exchange = self._dead_letter_exchange if attempts >= self._max_attempts else self._retry_exchange
            routing_key = self._dead_letter_routing_key if attempts >= self._max_attempts else self._retry_routing_key
            confirmed = self._channel.basic_publish(exchange, routing_key, body, properties=pika.BasicProperties(message_id=properties.message_id, headers=headers, delivery_mode=2, content_type="application/json"), mandatory=True)
            if confirmed is False:
                raise RuntimeError("RabbitMQ не подтвердил публикацию в retry-маршрут.")
            self._channel.basic_ack(method.delivery_tag)
            raise

    def topology_queue_names(self) -> tuple[str, str, str]:
        """Возвращает главную, retry- и dead-letter очередь подписчика.

        Метод нужен диагностическим адаптерам и интеграционным проверкам. Он
        не раскрывает предметную модель и не меняет состояние RabbitMQ.
        """
        return self._queue, self._retry_queue, self._dead_letter_queue

    def purge_owned_queues(self) -> None:
        """Удаляет доставки только из очередей данного подписчика.

        Операция применяется CLI с ``--reset`` для согласованного сброса
        локальной SQLite-базы и её демонстрационного транспорта. Exchange и
        очереди других подписчиков не изменяются.
        """
        for queue in self.topology_queue_names():
            self._channel.queue_purge(queue)

    def close(self) -> None:
        """Закрывает RabbitMQ-подключение consumer."""
        self._connection.close()
