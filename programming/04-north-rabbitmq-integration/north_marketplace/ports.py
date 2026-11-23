"""Порты прикладного слоя RabbitMQ-стадии.

Эти абстракции отделяют прикладные службы и process manager от SQLAlchemy,
RabbitMQ и структуры таблиц. Транспорт остаётся заменяемым инфраструктурным
адаптером, а агрегаты вообще не знают о нём.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ApplicationUnitOfWork(ABC):
    """Граница одной локальной транзакции состояния, фактов и inbox receipt."""

    @abstractmethod
    def __enter__(self) -> "ApplicationUnitOfWork":
        """Открывает транзакцию.

        Returns:
            Та же граница операции.
        """

    @abstractmethod
    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        """Фиксирует изменения или откатывает их.

        Args:
            error_type: Тип ошибки, если операция завершилась исключением.
            error: Сама ошибка, если она возникла.
            traceback: Трассировка ошибки, если она существует.
        """

    @abstractmethod
    def repositories(self) -> "RepositoryRegistry":
        """Выдаёт контекстный реестр репозиториев текущей операции."""

    @abstractmethod
    def processes(self) -> Any:
        """Выдаёт хранилище состояния длительного процесса."""

    @abstractmethod
    def process_commands(self) -> Any:
        """Выдаёт хранилище устойчивых команд процесса."""

    @abstractmethod
    def save_aggregate(self, context_name: str, aggregate_type: str, repository: Any, root: Any, expected_version: int | None, correlation_id: str, causation_id: str | None = None) -> int:
        """Сохраняет корень, факты и исходящие уведомления одной операцией."""

    @abstractmethod
    def is_handled(self, subscriber_name: str, position: int) -> bool:
        """Проверяет receipt polling-подписчика."""

    @abstractmethod
    def mark_handled(self, subscriber_name: str, position: int) -> None:
        """Фиксирует receipt polling-подписчика."""


class AggregateRepositoryPort(ABC):
    """Минимальный порт хранения одного вида предметных корней."""

    @abstractmethod
    def get(self, identifier: str) -> Any:
        """Возвращает корень с версией либо отсутствие результата."""

    @abstractmethod
    def save(self, root: Any, expected_version: int | None = None) -> int:
        """Сохраняет корень с ожидаемой оптимистической версией."""


class StockItemRepositoryPort(AggregateRepositoryPort):
    """Порт склада с выборкой кандидатов по товарному коду."""

    @abstractmethod
    def find_by_sku(self, sku: str) -> list[Any]:
        """Возвращает кандидаты для предметного распределения."""


class RepositoryRegistry(ABC):
    """Выдаёт контекстные репозитории одной локальной операции."""

    def __getitem__(self, context_name: str) -> AggregateRepositoryPort:
        """Возвращает порт по прежнему строковому ключу.

        Args:
            context_name: Имя ограниченного контекста.

        Returns:
            Соответствующий контекстный порт.

        Raises:
            KeyError: Имя контекста не входит в реестр.

        Note:
            Индексация сохранена как переходный совместимый путь для вызовов
            предыдущей стадии. Прикладные службы используют явные методы.
        """
        ports = {
            "catalog": self.catalog,
            "ordering": self.ordering,
            "inventory": self.inventory,
            "payment": self.payment,
            "shipping": self.shipping,
        }
        if context_name not in ports:
            raise KeyError(f"Неизвестный контекст: {context_name}")
        return ports[context_name]()

    @abstractmethod
    def catalog(self) -> AggregateRepositoryPort:
        """Возвращает порт Catalog."""

    @abstractmethod
    def ordering(self) -> AggregateRepositoryPort:
        """Возвращает порт Ordering."""

    @abstractmethod
    def inventory(self) -> StockItemRepositoryPort:
        """Возвращает порт Inventory."""

    @abstractmethod
    def payment(self) -> AggregateRepositoryPort:
        """Возвращает порт Payment."""

    @abstractmethod
    def shipping(self) -> AggregateRepositoryPort:
        """Возвращает порт Shipping."""


class UnitOfWorkFactory(ABC):
    """Создаёт локальные транзакционные границы прикладных команд."""

    @abstractmethod
    def unit_of_work(self) -> ApplicationUnitOfWork:
        """Создаёт ещё не открытую транзакционную границу."""


class NotificationReader(ABC):
    """Читает плоские уведомления из единого журнала фактов."""

    @abstractmethod
    def notifications_for(self, subscriber_name: str, event_types: set[str], limit: int) -> list[Any]:
        """Возвращает ещё не обработанные уведомления в глобальном порядке."""


class PublishedOfferReader(ABC):
    """Читает опубликованный скалярный контракт Catalog для Ordering."""

    @abstractmethod
    def published_offer(self, item_id: str) -> Any:
        """Возвращает опубликованные значения без объекта ``CatalogItem``."""


class OrderShipmentReader(ABC):
    """Читает скалярный контракт размещённого заказа для Shipping."""

    @abstractmethod
    def shipment_contract(self, order_id: str) -> Any:
        """Возвращает адрес и строки без объекта ``Order``."""
