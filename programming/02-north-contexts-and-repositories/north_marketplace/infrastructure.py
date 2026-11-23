"""SQLAlchemy-строки, Data Mapper и репозитории для актуального состояния."""

from __future__ import annotations

import json
from typing import Generic, TypeVar

from sqlalchemy import Integer, String, Text, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .catalog import CatalogItem
from .kernel import ConcurrencyConflict
from .inventory import StockItem
from .ordering import Order
from .payment import Payment
from .shipping import Shipment
from .ports import AggregateRepositoryPort, ApplicationUnitOfWork, RepositoryRegistry, StockItemRepositoryPort, UnitOfWorkFactory


class Base(DeclarativeBase):
    """Базовый класс инфраструктурных ORM-строк; домен его не импортирует."""


class StateRow(Base):
    """Общая форма строки с JSON-состоянием и оптимистической версией."""

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
    """Переводит обычный агрегат в ORM-строку и обратно без загрязнения домена."""

    def __init__(self, root_type: type[Root], row_type: type[Row]) -> None:
        """Фиксирует типы предметного корня и его инфраструктурной строки."""
        self._root_type, self._row_type = root_type, row_type

    def to_row(self, root: Root, version: int) -> Row:
        """Создаёт ORM-строку из состояния агрегата.

        Args:
            root: Обычный предметный корень с ``to_state``.
            version: Новая ожидаемая версия.

        Returns:
            Строка SQLAlchemy, не выдаваемая домену.
        """
        state = root.to_state()  # type: ignore[attr-defined]
        return self._row_type(id=state["id"], version=version, state_json=json.dumps(state, ensure_ascii=False))

    def to_root(self, row: Row) -> Root:
        """Восстанавливает обычный корень без возврата ORM-объекта."""
        return self._root_type.from_state(json.loads(row.state_json))  # type: ignore[attr-defined, no-any-return]


class AggregateRepository(Generic[Root, Row], AggregateRepositoryPort):
    """Репозиторий одного типа агрегатов с оптимистической версией."""

    def __init__(self, session: Session, mapper: JsonAggregateDataMapper[Root, Row], row_type: type[Row]) -> None:
        """Связывает короткую транзакцию с конкретным Data Mapper."""
        self._session, self._mapper, self._row_type = session, mapper, row_type

    def get(self, identifier: str) -> tuple[Root, int] | None:
        """Возвращает агрегат и версию либо ``None``."""
        row = self._session.get(self._row_type, identifier)
        return None if row is None else (self._mapper.to_root(row), row.version)

    def save(self, root: Root, expected_version: int | None = None) -> int:
        """Сохраняет новое актуальное состояние.

        Args:
            root: Корень после одной предметной команды.
            expected_version: Версия прочитанного состояния или ``None`` для создания.

        Returns:
            Версия после записи.

        Raises:
            ValueError: Корень уже существует или версия устарела.
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
    """Контекстный репозиторий корней CatalogItem."""


class OrderRepository(AggregateRepository[Order, OrderRow]):
    """Контекстный репозиторий корней Order."""


class StockItemRepository(AggregateRepository[StockItem, StockItemRow], StockItemRepositoryPort):
    """Контекстный репозиторий StockItem с выборкой по SKU."""

    def find_by_sku(self, sku: str) -> list[tuple[StockItem, int]]:
        """Возвращает обычные складские корни, подходящие товарному коду."""
        result: list[tuple[StockItem, int]] = []
        for row in self._session.execute(select(StockItemRow)).scalars().all():
            state = json.loads(row.state_json)
            if state["sku"] == sku:
                result.append((self._mapper.to_root(row), row.version))
        return result


class PaymentRepository(AggregateRepository[Payment, PaymentRow]):
    """Контекстный репозиторий корней Payment."""


class ShipmentRepository(AggregateRepository[Shipment, ShipmentRow]):
    """Контекстный репозиторий корней Shipment."""


class SqlAlchemyRepositoryRegistry(RepositoryRegistry):
    """Собирает контекстные SQLAlchemy-адаптеры для одной сессии."""

    def __init__(self, session: Session) -> None:
        """Создаёт репозитории, разделяющие одну локальную транзакцию."""
        self._catalog = CatalogItemRepository(session, JsonAggregateDataMapper(CatalogItem, CatalogItemRow), CatalogItemRow)
        self._ordering = OrderRepository(session, JsonAggregateDataMapper(Order, OrderRow), OrderRow)
        self._inventory = StockItemRepository(session, JsonAggregateDataMapper(StockItem, StockItemRow), StockItemRow)
        self._payment = PaymentRepository(session, JsonAggregateDataMapper(Payment, PaymentRow), PaymentRow)
        self._shipping = ShipmentRepository(session, JsonAggregateDataMapper(Shipment, ShipmentRow), ShipmentRow)

    def catalog(self) -> CatalogItemRepository:
        """Возвращает адаптер хранения предложений."""
        return self._catalog

    def ordering(self) -> OrderRepository:
        """Возвращает адаптер хранения заказов."""
        return self._ordering

    def inventory(self) -> StockItemRepository:
        """Возвращает адаптер хранения складских позиций."""
        return self._inventory

    def payment(self) -> PaymentRepository:
        """Возвращает адаптер хранения платежей."""
        return self._payment

    def shipping(self) -> ShipmentRepository:
        """Возвращает адаптер хранения отправлений."""
        return self._shipping


class UnitOfWork(ApplicationUnitOfWork):
    """Адаптер порта локальной транзакции поверх одной SQLAlchemy-сессии."""

    def __init__(self, database: "Database") -> None:
        """Сохраняет фабрику сессий инфраструктуры."""
        self._database = database
        self._session: Session | None = None

    def __enter__(self) -> "UnitOfWork":
        """Открывает сессию для одной прикладной команды."""
        self._session = self._database.session()
        return self

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        """Фиксирует операцию либо откатывает её при исключении."""
        assert self._session is not None
        try:
            if error_type is None:
                self._session.commit()
            else:
                self._session.rollback()
        finally:
            self._session.close()

    def repositories(self) -> RepositoryRegistry:
        """Создаёт репозитории в границах открытой сессии."""
        if self._session is None:
            raise RuntimeError("Unit of Work ещё не открыт.")
        return SqlAlchemyRepositoryRegistry(self._session)


class Database(UnitOfWorkFactory):
    """Создаёт SQLite-схему и короткие сессии инфраструктуры."""

    def __init__(self, url: str = "sqlite+pysqlite:///north-stage-02.db") -> None:
        """Открывает движок SQLAlchemy и создаёт таблицы актуального состояния."""
        options = {"future": True}
        if url.endswith(":memory:"):
            options.update({"connect_args": {"check_same_thread": False}, "poolclass": StaticPool})
        self._engine = create_engine(url, **options)
        Base.metadata.create_all(self._engine)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def session(self) -> Session:
        """Возвращает новую сессию для одного прикладного вызова."""
        return self._sessions()

    def unit_of_work(self) -> UnitOfWork:
        """Создаёт порт одной локальной прикладной операции."""
        return UnitOfWork(self)


def repositories(session: Session) -> dict[str, AggregateRepository]:
    """Создаёт набор контекстных репозиториев на границе прикладного слоя."""
    return {
        "catalog": CatalogItemRepository(session, JsonAggregateDataMapper(CatalogItem, CatalogItemRow), CatalogItemRow),
        "ordering": OrderRepository(session, JsonAggregateDataMapper(Order, OrderRow), OrderRow),
        "inventory": StockItemRepository(session, JsonAggregateDataMapper(StockItem, StockItemRow), StockItemRow),
        "payment": PaymentRepository(session, JsonAggregateDataMapper(Payment, PaymentRow), PaymentRow),
        "shipping": ShipmentRepository(session, JsonAggregateDataMapper(Shipment, ShipmentRow), ShipmentRow),
    }
