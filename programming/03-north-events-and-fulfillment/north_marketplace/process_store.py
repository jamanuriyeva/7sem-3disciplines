"""Сохраняемое состояние FulfillmentProcess и его устойчивые команды."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, String, Text, or_, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .kernel import ConcurrencyConflict
from .persistence import Base, StateRow


class FulfillmentProcessRow(StateRow):
    """Текущее состояние process manager, отдельное от агрегатов контекстов."""

    __tablename__ = "fulfillment_processes"


class ProcessCommandRow(Base):
    """Устойчивый work-item следующей локальной команды process manager."""

    __tablename__ = "fulfillment_process_commands"
    command_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    process_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    leased_until: Mapped[str | None] = mapped_column(String(48), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ClaimedProcessCommand:
    """Команда, захваченная одним диспетчером на ограниченное время."""

    def __init__(self, command_id: str, process_id: str, command: dict, lease_token: str) -> None:
        """Сохраняет данные команды без выдачи ORM-строки прикладному слою."""
        self.command_id = command_id
        self.process_id = process_id
        self.command = dict(command)
        self.lease_token = lease_token


class FulfillmentProcessRepository:
    """Восстанавливает и сохраняет состояние одного длительного процесса."""

    def __init__(self, session: Session) -> None:
        """Получает сессию Unit of Work, а не открывает новое соединение."""
        self._session = session

    def get(self, order_id: str):
        """Возвращает процесс по идентификатору заказа либо ``None``."""
        from .fulfillment import FulfillmentProcess

        row = self._session.get(FulfillmentProcessRow, order_id)
        if row is None:
            return None
        process = FulfillmentProcess.from_state(json.loads(row.state_json))
        process._persistence_version = row.version
        return process

    def save(self, process) -> None:
        """Сохраняет процесс с оптимистической защитой версии."""
        current = self._session.get(FulfillmentProcessRow, process.order_id.value())
        state_json = json.dumps(process.to_state(), ensure_ascii=False)
        if current is None:
            self._session.add(FulfillmentProcessRow(id=process.order_id.value(), version=1, state_json=state_json))
            process._persistence_version = 1
            return
        expected = getattr(process, "_persistence_version", None)
        if expected is None:
            raise ConcurrencyConflict("Процесс сохранён без ожидаемой версии.")
        result = self._session.execute(
            update(FulfillmentProcessRow)
            .where(FulfillmentProcessRow.id == process.order_id.value(), FulfillmentProcessRow.version == expected)
            .values(state_json=state_json, version=expected + 1)
        )
        if result.rowcount != 1:
            raise ConcurrencyConflict("Процесс обеспечения изменён конкурентно.")
        process._persistence_version = expected + 1
        self._session.expire(current)


class ProcessCommandRepository:
    """Сохраняет и безопасно захватывает команды process manager."""

    def __init__(self, session: Session) -> None:
        """Получает сессию активного Unit of Work."""
        self._session = session

    def enqueue(self, command_id: str, process_id: str, command: dict) -> None:
        """Добавляет команду в транзакции состояния процесса."""
        if self._session.get(ProcessCommandRow, command_id) is None:
            self._session.add(ProcessCommandRow(command_id=command_id, process_id=process_id, payload_json=json.dumps(command, ensure_ascii=False), status="PENDING", attempts=0, version=1))

    def claim(self, limit: int = 100, lease_seconds: int = 30) -> list[ClaimedProcessCommand]:
        """Условно захватывает ожидающие или просроченные команды."""
        now = datetime.now(UTC)
        until = (now + timedelta(seconds=lease_seconds)).isoformat()
        rows = self._session.execute(
            select(ProcessCommandRow)
            .where(or_(ProcessCommandRow.status == "PENDING", (ProcessCommandRow.status == "LEASED") & (ProcessCommandRow.leased_until < now.isoformat())))
            .order_by(ProcessCommandRow.command_id)
            .limit(limit)
        ).scalars().all()
        claimed: list[ClaimedProcessCommand] = []
        for row in rows:
            token = str(uuid.uuid4())
            result = self._session.execute(
                update(ProcessCommandRow)
                .where(ProcessCommandRow.command_id == row.command_id, ProcessCommandRow.version == row.version, or_(ProcessCommandRow.status == "PENDING", (ProcessCommandRow.status == "LEASED") & (ProcessCommandRow.leased_until < now.isoformat())))
                .values(status="LEASED", lease_token=token, leased_until=until, attempts=row.attempts + 1, version=row.version + 1)
            )
            if result.rowcount == 1:
                claimed.append(ClaimedProcessCommand(row.command_id, row.process_id, json.loads(row.payload_json), token))
        return claimed

    def complete(self, command_id: str, lease_token: str) -> None:
        """Отмечает завершённой только команду, всё ещё принадлежащую relay."""
        self._session.execute(update(ProcessCommandRow).where(ProcessCommandRow.command_id == command_id, ProcessCommandRow.status == "LEASED", ProcessCommandRow.lease_token == lease_token).values(status="COMPLETED", lease_token=None, leased_until=None, version=ProcessCommandRow.version + 1))

    def release(self, command_id: str, lease_token: str) -> None:
        """Возвращает неисполненную команду в очередь после ошибки вызова."""
        self._session.execute(update(ProcessCommandRow).where(ProcessCommandRow.command_id == command_id, ProcessCommandRow.status == "LEASED", ProcessCommandRow.lease_token == lease_token).values(status="PENDING", lease_token=None, leased_until=None, version=ProcessCommandRow.version + 1))
