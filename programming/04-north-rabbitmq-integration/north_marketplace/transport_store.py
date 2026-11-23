"""Строки transactional outbox и consumer inbox RabbitMQ-адаптера."""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .persistence import Base


class OutboxRow(Base):
    """Исходящий транспортный контракт, сохранённый вместе с фактом."""

    __tablename__ = "notification_outbox"
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    global_position: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    routing_key: Mapped[str] = mapped_column(String(192), nullable=False)
    body_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    leased_until: Mapped[str | None] = mapped_column(String(48), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class InboxReceiptRow(Base):
    """Ключ успешно принятого RabbitMQ-сообщения одного потребителя."""

    __tablename__ = "consumer_inbox"
    consumer_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_on: Mapped[str] = mapped_column(String(48), nullable=False)
