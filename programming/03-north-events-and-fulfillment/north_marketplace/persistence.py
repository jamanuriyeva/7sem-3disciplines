"""Актуальное состояние агрегатов, Data Mapper и SQLAlchemy-репозитории.

Этот модуль хранит только состояние, из которого восстанавливаются обычные
агрегаты. Он не знает о журнале фактов, уведомлениях и длительном процессе.
"""

from __future__ import annotations

import json
from typing import Generic, TypeVar

from sqlalchemy import Integer, String, Text, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .catalog import CatalogItem
from .inventory import StockItem
from .kernel import ConcurrencyConflict
from .ordering import Order
from .payment import Payment
from .ports import AggregateRepositoryPort, RepositoryRegistry, StockItemRepositoryPort
from .shipping import Shipment


class Base(DeclarativeBase):
    """Базовый класс исключительно инфраструктурных ORM-строк."""


class StateRow(Base):
    """Общая форма актуального состояния одного агрегата или процесса."""

    __abstract__ = True
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)


class CatalogItemRow(StateRow):
    """ORM-строка контекста Catalog."""

    __tablename__ = "catalog_items"


class OrderRow(StateRow):
    """ORM-строка контекста Ordering."""

    __tablename__ = "ordering_orders"


class StockItemRow(StateRow):
    """ORM-строка контекста Inventory."""

    __tablename__ = "inventory_stock_items"


class PaymentRow(StateRow):
    """ORM-строка контекста Payment."""

    __tablename__ = "payment_payments"


class ShipmentRow(StateRow):
    """ORM-строка контекста Shipping."""

    __tablename__ = "shipping_shipments"


Root = TypeVar("Root")
Row = TypeVar("Row", bound=StateRow)


class JsonAggregateDataMapper(Generic[Root, Row]):
    """Переводит обычный агрегат в ORM-строку и обратно."""

    def __init__(self, root_type: type[Root], row_type: type[Row]) -> None:
        """Фиксирует предметный и инфраструктурный типы."""
        self._root_type, self._row_type = root_type, row_type

    def to_row(self, root: Root, version: int) -> Row:
        """Создаёт инфраструктурную строку из текущего состояния корня."""
        state = root.to_state()  # type: ignore[attr-defined]
        return self._row_type(id=state["id"], version=version, state_json=json.dumps(state, ensure_ascii=False))

    def to_root(self, row: Row) -> Root:
        """Восстанавливает предметный корень, а не ORM-объект."""
        return self._root_type.from_state(json.loads(row.state_json))  # type: ignore[attr-defined, no-any-return]


class AggregateRepository(Generic[Root, Row], AggregateRepositoryPort):
    """Хранилище одного типа агрегатов с оптимистической версией."""

    def __init__(self, session: Session, mapper: JsonAggregateDataMapper[Root, Row], row_type: type[Row]) -> None:
        """Связывает mapper с короткой локальной транзакцией."""
        self._session, self._mapper, self._row_type = session, mapper, row_type

    def get(self, identifier: str) -> tuple[Root, int] | None:
        """Возвращает восстановленный корень и его версию."""
        row = self._session.get(self._row_type, identifier)
        return None if row is None else (self._mapper.to_root(row), row.version)

    def save(self, root: Root, expected_version: int | None = None) -> int:
        """Сохраняет состояние условным обновлением ``id`` и версии.

        Raises:
            ConcurrencyConflict: Ожидаемая версия больше не является текущей.
        """
        state = root.to_state()  # type: ignore[attr-defined]
        current = self._session.get(self._row_type, state["id"])
        if current is None:
            if expected_version is not None:
                raise ConcurrencyConflict("Агрегат отсутствует при проверке версии.")
            self._session.add(self._mapper.to_row(root, 1))
            return 1
        if expected_version is None:
            raise ConcurrencyConflict("Для изменения агрегата требуется ожидаемая версия.")
        result = self._session.execute(
            update(self._row_type)
            .where(self._row_type.id == state["id"], self._row_type.version == expected_version)
            .values(state_json=json.dumps(state, ensure_ascii=False), version=expected_version + 1)
        )
        if result.rowcount != 1:
            raise ConcurrencyConflict("Агрегат был изменён конкурентной командой.")
        self._session.expire(current)
        return expected_version + 1


class CatalogItemRepository(AggregateRepository[CatalogItem, CatalogItemRow]):
    """Порт хранения корня CatalogItem, реализованный SQLAlchemy-адаптером."""


class OrderRepository(AggregateRepository[Order, OrderRow]):
    """Порт хранения корня Order, реализованный SQLAlchemy-адаптером."""


class StockItemRepository(AggregateRepository[StockItem, StockItemRow], StockItemRepositoryPort):
    """Порт хранения StockItem с выборкой кандидатов по товарному коду."""

    def find_by_sku(self, sku: str) -> list[tuple[StockItem, int]]:
        """Возвращает обычные складские корни, а не ORM-строки."""
        result: list[tuple[StockItem, int]] = []
        for row in self._session.execute(select(StockItemRow)).scalars().all():
            state = json.loads(row.state_json)
            if state["sku"] == sku:
                result.append((self._mapper.to_root(row), row.version))
        return result


class PaymentRepository(AggregateRepository[Payment, PaymentRow]):
    """Порт хранения корня Payment, реализованный SQLAlchemy-адаптером."""


class ShipmentRepository(AggregateRepository[Shipment, ShipmentRow]):
    """Порт хранения корня Shipment, реализованный SQLAlchemy-адаптером."""


class SqlAlchemyRepositoryRegistry(RepositoryRegistry):
    """Собирает контекстные SQLAlchemy-репозитории в одной сессии."""

    def __init__(self, session: Session) -> None:
        """Создаёт адаптеры, разделяющие локальную транзакцию."""
        self._catalog = CatalogItemRepository(session, JsonAggregateDataMapper(CatalogItem, CatalogItemRow), CatalogItemRow)
        self._ordering = OrderRepository(session, JsonAggregateDataMapper(Order, OrderRow), OrderRow)
        self._inventory = StockItemRepository(session, JsonAggregateDataMapper(StockItem, StockItemRow), StockItemRow)
        self._payment = PaymentRepository(session, JsonAggregateDataMapper(Payment, PaymentRow), PaymentRow)
        self._shipping = ShipmentRepository(session, JsonAggregateDataMapper(Shipment, ShipmentRow), ShipmentRow)

    def catalog(self) -> CatalogItemRepository:
        """Возвращает адаптер Catalog."""
        return self._catalog

    def ordering(self) -> OrderRepository:
        """Возвращает адаптер Ordering."""
        return self._ordering

    def inventory(self) -> StockItemRepository:
        """Возвращает адаптер Inventory."""
        return self._inventory

    def payment(self) -> PaymentRepository:
        """Возвращает адаптер Payment."""
        return self._payment

    def shipping(self) -> ShipmentRepository:
        """Возвращает адаптер Shipping."""
        return self._shipping


def repositories(session: Session) -> dict[str, AggregateRepository]:
    """Создаёт контекстные репозитории для одной сессии Unit of Work."""
    return {
        "catalog": CatalogItemRepository(session, JsonAggregateDataMapper(CatalogItem, CatalogItemRow), CatalogItemRow),
        "ordering": OrderRepository(session, JsonAggregateDataMapper(Order, OrderRow), OrderRow),
        "inventory": StockItemRepository(session, JsonAggregateDataMapper(StockItem, StockItemRow), StockItemRow),
        "payment": PaymentRepository(session, JsonAggregateDataMapper(Payment, PaymentRow), PaymentRow),
        "shipping": ShipmentRepository(session, JsonAggregateDataMapper(Shipment, ShipmentRow), ShipmentRow),
    }
