"""Append-only журнал доменных фактов и receipt polling-подписчиков."""

from __future__ import annotations

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .persistence import Base


class DomainEventRow(Base):
    """Одна append-only запись общего журнала доменных фактов."""

    __tablename__ = "domain_events"
    __table_args__ = (UniqueConstraint("context_name", "aggregate_type", "aggregate_id", "stream_version", name="uq_event_stream_version"),)
    global_position: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    context_name: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(96), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(120), nullable=False)
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_on: Mapped[str] = mapped_column(String(48), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class HandledNotificationRow(Base):
    """Receipt polling-подписчика, сохраняемый с локальной реакцией."""

    __tablename__ = "handled_notifications"
    subscriber_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    global_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    handled_on: Mapped[str] = mapped_column(String(48), nullable=False)
