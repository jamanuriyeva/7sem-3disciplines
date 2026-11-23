"""Порты прикладного слоя для работы с локальной транзакцией.

Порты намеренно малы: прикладная служба знает, что ей доступны репозитории в
границах одной операции, но не знает ни SQLAlchemy, ни SQLite, ни форму строк
хранения. Конкретная реализация находится в инфраструктурном адаптере.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ApplicationUnitOfWork(ABC):
    """Граница одной локальной операции прикладной службы.

    Реализация фиксирует все изменения при нормальном выходе из контекстного
    менеджера и откатывает их при исключении.
    """

    @abstractmethod
    def __enter__(self) -> "ApplicationUnitOfWork":
        """Открывает локальную транзакцию.

        Returns:
            Та же граница операции.
        """

    @abstractmethod
    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        """Фиксирует или отменяет локальную операцию.

        Args:
            error_type: Тип исключения, если операция завершилась ошибкой.
            error: Экземпляр исключения, если он возник.
            traceback: Трассировка исключения, если она существует.
        """

    @abstractmethod
    def repositories(self) -> "RepositoryRegistry":
        """Возвращает контекстные репозитории текущей операции.

        Returns:
            Контекстный реестр репозиториев, выдающих обычные предметные корни.
        """


class AggregateRepositoryPort(ABC):
    """Минимальный порт хранения одного вида корней."""

    @abstractmethod
    def get(self, identifier: str) -> Any:
        """Возвращает корень и версию либо отсутствие результата."""

    @abstractmethod
    def save(self, root: Any, expected_version: int | None = None) -> int:
        """Сохраняет корень с ожидаемой оптимистической версией."""


class StockItemRepositoryPort(AggregateRepositoryPort):
    """Порт склада с запросом кандидатов по товарному коду."""

    @abstractmethod
    def find_by_sku(self, sku: str) -> list[Any]:
        """Возвращает кандидаты для предметной службы распределения."""


class RepositoryRegistry(ABC):
    """Набор контекстных портов, доступных внутри одной операции."""

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
        """Возвращает порт контекста Catalog."""

    @abstractmethod
    def ordering(self) -> AggregateRepositoryPort:
        """Возвращает порт контекста Ordering."""

    @abstractmethod
    def inventory(self) -> StockItemRepositoryPort:
        """Возвращает порт контекста Inventory."""

    @abstractmethod
    def payment(self) -> AggregateRepositoryPort:
        """Возвращает порт контекста Payment."""

    @abstractmethod
    def shipping(self) -> AggregateRepositoryPort:
        """Возвращает порт контекста Shipping."""


class UnitOfWorkFactory(ABC):
    """Создаёт независимую локальную транзакцию для прикладной команды."""

    @abstractmethod
    def unit_of_work(self) -> ApplicationUnitOfWork:
        """Создаёт ещё не открытую границу прикладной операции.

        Returns:
            Порт локальной транзакции.
        """


class PublishedOfferReader(ABC):
    """Читает опубликованный скалярный контракт Catalog для Ordering."""

    @abstractmethod
    def published_offer(self, item_id: str) -> Any:
        """Возвращает опубликованные значения без выдачи ``CatalogItem``."""


class OrderShipmentReader(ABC):
    """Читает скалярный контракт размещённого заказа для Shipping."""

    @abstractmethod
    def shipment_contract(self, order_id: str) -> Any:
        """Возвращает адрес и строки без выдачи ``Order``."""
