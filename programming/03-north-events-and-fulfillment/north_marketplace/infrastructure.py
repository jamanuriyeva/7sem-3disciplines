"""Фасад локальной транзакции событийной стадии.

Актуальное состояние, Data Mapper, append-only журнал и состояние процесса
разделены по инфраструктурным обязанностям. Этот модуль сохраняет прежний
публичный импорт ``Database`` и собирает их в Unit of Work.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .event_log import DomainEventRow, HandledNotificationRow
from .events import DomainEvent, Notification
from .persistence import AggregateRepository, Base, SqlAlchemyRepositoryRegistry, repositories
from .ports import ApplicationUnitOfWork, NotificationReader, RepositoryRegistry, UnitOfWorkFactory
from .process_store import FulfillmentProcessRepository, ProcessCommandRepository


class UnitOfWork(ApplicationUnitOfWork):
    """Граница одной локальной транзакции контекста и его фактов."""

    def __init__(self, database: "Database") -> None:
        """Создаёт закрытую до входа SQLAlchemy-сессию."""
        self._database = database
        self._session: Session | None = None
        self._roots_to_clear: list[object] = []

    def __enter__(self) -> "UnitOfWork":
        """Открывает сессию для одного прикладного действия."""
        self._session = self._database._sessions()
        return self

    def __exit__(self, error_type, error, traceback) -> None:
        """Фиксирует всё вместе либо откатывает всё вместе."""
        assert self._session is not None
        try:
            if error_type is None:
                self._session.commit()
                for root in self._roots_to_clear:
                    root.clear_pending_events()  # type: ignore[attr-defined]
            else:
                self._session.rollback()
        finally:
            self._session.close()

    @property
    def session(self) -> Session:
        """Возвращает сессию только внутри контекстного менеджера."""
        if self._session is None:
            raise RuntimeError("Unit of Work ещё не открыт.")
        return self._session

    def repositories(self) -> RepositoryRegistry:
        """Создаёт репозитории текущей локальной транзакции."""
        return SqlAlchemyRepositoryRegistry(self.session)

    def processes(self) -> FulfillmentProcessRepository:
        """Возвращает хранилище process manager в той же транзакции."""
        return FulfillmentProcessRepository(self.session)

    def process_commands(self) -> ProcessCommandRepository:
        """Возвращает хранилище устойчивых команд процесса."""
        return ProcessCommandRepository(self.session)

    def save_aggregate(self, context_name: str, aggregate_type: str, repository: AggregateRepository, root, expected_version: int | None, correlation_id: str, causation_id: str | None = None) -> int:
        """Сохраняет состояние и новые факты одного корня атомарно."""
        version = repository.save(root, expected_version)
        for event in root.pending_events():
            self._append_event(context_name, aggregate_type, root.id().value(), event, correlation_id, causation_id)
        self._roots_to_clear.append(root)
        return version

    def is_handled(self, subscriber_name: str, position: int) -> bool:
        """Проверяет receipt внутри той же транзакции обработчика."""
        return self.session.get(HandledNotificationRow, {"subscriber_name": subscriber_name, "global_position": position}) is not None

    def mark_handled(self, subscriber_name: str, position: int) -> None:
        """Добавляет receipt, откатываемый вместе с предметной командой."""
        self.session.add(HandledNotificationRow(subscriber_name=subscriber_name, global_position=position, handled_on=datetime.now(UTC).isoformat()))

    def _append_event(self, context_name: str, aggregate_type: str, aggregate_id: str, event: DomainEvent, correlation_id: str, causation_id: str | None) -> None:
        """Добавляет новую строку потока в общий append-only журнал."""
        last = self.session.execute(select(DomainEventRow.stream_version).where(DomainEventRow.context_name == context_name, DomainEventRow.aggregate_type == aggregate_type, DomainEventRow.aggregate_id == aggregate_id).order_by(DomainEventRow.stream_version.desc()).limit(1)).scalar_one_or_none()
        self.session.add(DomainEventRow(event_id=str(uuid.uuid4()), context_name=context_name, aggregate_type=aggregate_type, aggregate_id=aggregate_id, stream_version=(last or 0) + 1, occurred_on=event.occurred_on(), event_type=event.event_type, correlation_id=correlation_id, causation_id=causation_id, payload_json=json.dumps(event.payload(), ensure_ascii=False)))


class Database(UnitOfWorkFactory, NotificationReader):
    """Создаёт SQLite-схему актуального состояния, журнала и receipts."""

    def __init__(self, url: str = "sqlite+pysqlite:///north-stage-03.db") -> None:
        """Открывает SQLAlchemy-движок и создаёт локальные таблицы."""
        options = {"future": True}
        if url.endswith(":memory:"):
            options.update({"connect_args": {"check_same_thread": False}, "poolclass": StaticPool})
        self._engine = create_engine(url, **options)
        Base.metadata.create_all(self._engine)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def unit_of_work(self) -> UnitOfWork:
        """Создаёт границу одной локальной транзакции."""
        return UnitOfWork(self)

    def notifications_for(self, subscriber_name: str, event_types: set[str], limit: int) -> list[Notification]:
        """Читает интересные подписчику необработанные строки журнала."""
        if not event_types:
            return []
        with self._sessions() as session:
            handled = select(HandledNotificationRow.global_position).where(HandledNotificationRow.subscriber_name == subscriber_name)
            rows = session.execute(select(DomainEventRow).where(DomainEventRow.event_type.in_(event_types), DomainEventRow.global_position.not_in(handled)).order_by(DomainEventRow.global_position).limit(limit)).scalars().all()
            return [Notification(row.global_position, row.event_id, row.context_name, row.event_type, row.aggregate_id, row.correlation_id, row.causation_id, json.loads(row.payload_json)) for row in rows]
