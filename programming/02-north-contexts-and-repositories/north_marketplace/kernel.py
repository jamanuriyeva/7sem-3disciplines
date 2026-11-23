"""Общие предметные типы без зависимостей от транспорта и хранения."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class DomainRuleViolation(ValueError):
    """Сообщает о нарушенном инварианте предметной модели."""


class ConcurrencyConflict(RuntimeError):
    """Сообщает, что актуальное состояние изменено другой командой.

    Это не нарушение предметного правила агрегата. Ошибка означает, что
    прикладная операция должна перечитать корень и повторить решение на его
    новом состоянии, если такое повторение допустимо предметной политикой.
    """


class Identifier:
    """Представляет типизированный неизменяемый идентификатор."""

    def __init__(self, value: str) -> None:
        """Проверяет и нормализует строковый идентификатор.

        Args:
            value: Внешнее строковое значение.

        Raises:
            DomainRuleViolation: Значение пусто.
        """
        if not isinstance(value, str) or not value.strip():
            raise DomainRuleViolation("Идентификатор не может быть пустым.")
        self._value = value.strip()

    def value(self) -> str:
        """Возвращает нормализованное значение."""
        return self._value

    def __eq__(self, other: object) -> bool:
        """Сравнивает идентификаторы только одинаковых типов."""
        return type(self) is type(other) and isinstance(other, Identifier) and self._value == other._value

    def __hash__(self) -> int:
        """Возвращает хеш конкретного типа и значения."""
        return hash((type(self), self._value))


class Sku:
    """Представляет товарный код без отдельной идентичности."""

    def __init__(self, value: str) -> None:
        """Нормализует непустой код товара.

        Args:
            value: Исходный код товара.
        """
        if not isinstance(value, str) or not value.strip():
            raise DomainRuleViolation("Код товара обязателен.")
        self._value = value.strip().upper()

    def value(self) -> str:
        """Возвращает код в верхнем регистре."""
        return self._value


class Quantity:
    """Представляет строго положительное количество единиц."""

    def __init__(self, value: int) -> None:
        """Проверяет положительное целое число.

        Args:
            value: Количество единиц.
        """
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise DomainRuleViolation("Количество должно быть положительным целым числом.")
        self._value = value

    def value(self) -> int:
        """Возвращает количество."""
        return self._value


class Money:
    """Представляет неотрицательную денежную величину в одной валюте."""

    def __init__(self, amount: Decimal | str, currency: str) -> None:
        """Создаёт точную денежную величину.

        Args:
            amount: Сумма, приводимая к ``Decimal``.
            currency: Трёхбуквенный код валюты.

        Raises:
            DomainRuleViolation: Сумма или валюта недопустимы.
        """
        try:
            value = Decimal(str(amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as error:
            raise DomainRuleViolation("Сумма имеет неверный формат.") from error
        if value < 0:
            raise DomainRuleViolation("Сумма не может быть отрицательной.")
        if not isinstance(currency, str) or len(currency.strip()) != 3 or not currency.strip().isalpha():
            raise DomainRuleViolation("Валюта должна состоять из трёх букв.")
        self._amount, self._currency = value, currency.strip().upper()

    def amount(self) -> Decimal:
        """Возвращает точную сумму."""
        return self._amount

    def currency(self) -> str:
        """Возвращает код валюты."""
        return self._currency

    def multiply(self, quantity: Quantity) -> "Money":
        """Умножает цену на количество.

        Args:
            quantity: Число физических единиц.

        Returns:
            Новая сумма той же валюты.
        """
        return Money(self._amount * quantity.value(), self._currency)


class BuyerId(Identifier):
    """Идентификатор покупателя вне пяти агрегатов."""


class SellerId(Identifier):
    """Идентификатор продавца вне пяти агрегатов."""


class WarehouseId(Identifier):
    """Идентификатор склада вне агрегата складской позиции."""


class OrderReference(Identifier):
    """Локальная типизированная ссылка на заказ другого контекста."""


class OrderLineReference(Identifier):
    """Локальная типизированная ссылка на строку заказа другого контекста."""


class PaymentReference(Identifier):
    """Локальная типизированная ссылка на платёж процесса обеспечения."""


class ShipmentReference(Identifier):
    """Локальная типизированная ссылка на отправление процесса обеспечения."""
