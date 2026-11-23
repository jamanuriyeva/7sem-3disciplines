"""Предметная модель первого агрегата заказа."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum


class DomainRuleViolation(ValueError):
    """Сообщает о нарушении правила, владельцем которого является агрегат."""


class Identifier:
    """Базовый типизированный идентификатор без самостоятельного поведения."""

    def __init__(self, value: str) -> None:
        """Нормализует непустое строковое значение.

        Args:
            value: Внешнее строковое представление идентификатора.

        Raises:
            DomainRuleViolation: Строка пуста либо не является строкой.
        """
        if not isinstance(value, str) or not value.strip():
            raise DomainRuleViolation("Идентификатор не может быть пустым.")
        self._value = value.strip()

    def value(self) -> str:
        """Возвращает нормализованное значение идентификатора."""
        return self._value

    def __eq__(self, other: object) -> bool:
        """Сравнивает идентификаторы только одного конкретного типа."""
        return type(self) is type(other) and isinstance(other, Identifier) and self._value == other._value

    def __hash__(self) -> int:
        """Возвращает хеш типа и значения."""
        return hash((type(self), self._value))


class OrderId(Identifier):
    """Идентифицирует корень агрегата заказа."""


class OrderLineId(Identifier):
    """Идентифицирует внутреннюю сущность строки заказа."""


class BuyerId(Identifier):
    """Ссылается по идентификатору на покупателя вне контекста Ordering."""


class Sku:
    """Представляет нормализованный код товарной позиции."""

    def __init__(self, value: str) -> None:
        """Создаёт непустой код в верхнем регистре.

        Args:
            value: Внешний код товара.

        Raises:
            DomainRuleViolation: Код отсутствует.
        """
        if not isinstance(value, str) or not value.strip():
            raise DomainRuleViolation("Код товара обязателен.")
        self._value = value.strip().upper()

    def value(self) -> str:
        """Возвращает нормализованный код."""
        return self._value


class Quantity:
    """Представляет положительное количество физических единиц."""

    def __init__(self, value: int) -> None:
        """Проверяет положительное целое количество.

        Args:
            value: Число единиц товара.

        Raises:
            DomainRuleViolation: Количество не положительно.
        """
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise DomainRuleViolation("Количество должно быть положительным целым числом.")
        self._value = value

    def value(self) -> int:
        """Возвращает количество."""
        return self._value


class Money:
    """Неизменяемая сумма в трёхбуквенной валюте."""

    def __init__(self, amount: Decimal | str, currency: str) -> None:
        """Создаёт неотрицательную денежную сумму.

        Args:
            amount: Сумма, преобразуемая в ``Decimal``.
            currency: Код валюты из трёх букв.

        Raises:
            DomainRuleViolation: Сумма либо валюта некорректны.
        """
        try:
            normalized = Decimal(str(amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as error:
            raise DomainRuleViolation("Сумма имеет неверный формат.") from error
        if normalized < 0:
            raise DomainRuleViolation("Сумма не может быть отрицательной.")
        if not isinstance(currency, str) or len(currency.strip()) != 3 or not currency.strip().isalpha():
            raise DomainRuleViolation("Валюта должна состоять из трёх букв.")
        self._amount = normalized
        self._currency = currency.strip().upper()

    def amount(self) -> Decimal:
        """Возвращает точную сумму."""
        return self._amount

    def currency(self) -> str:
        """Возвращает код валюты."""
        return self._currency

    def multiply(self, quantity: Quantity) -> "Money":
        """Вычисляет стоимость указанного количества.

        Args:
            quantity: Положительное количество единиц.

        Returns:
            Новая сумма в той же валюте.
        """
        return Money(self._amount * quantity.value(), self._currency)


class OrderStatus(Enum):
    """Перечисляет состояния минимального жизненного цикла заказа."""

    DRAFT = "DRAFT"
    PLACED = "PLACED"
    CANCELLED = "CANCELLED"


class OrderLine:
    """Внутренняя сущность строки, управляемая только корнем ``Order``."""

    def __init__(self, line_id: OrderLineId, sku: Sku, title: str, unit_price: Money, quantity: Quantity) -> None:
        """Создаёт внутреннюю строку из уже проверенных значений.

        Args:
            line_id: Идентичность строки внутри заказа.
            sku: Код продаваемого товара.
            title: Снимок названия на момент добавления.
            unit_price: Снимок цены на момент добавления.
            quantity: Количество товара.
        """
        self._id = line_id
        self._sku = sku
        self._title = title.strip()
        self._unit_price = unit_price
        self._quantity = quantity

    @classmethod
    def create_paid(cls, line_id: OrderLineId, sku: Sku, title: str, unit_price: Money, quantity: Quantity) -> "OrderLine":
        """Фабричный метод оплачиваемой строки.

        Args:
            line_id: Идентичность новой строки.
            sku: Код товара.
            title: Снимок названия.
            unit_price: Положительная цена снимка.
            quantity: Заказанное количество.

        Returns:
            Готовая внутренняя сущность.

        Raises:
            DomainRuleViolation: Название пусто либо цена не положительна.
        """
        if not isinstance(title, str) or not title.strip():
            raise DomainRuleViolation("Название товара обязательно.")
        if unit_price.amount() <= 0:
            raise DomainRuleViolation("Оплачиваемая строка требует положительную цену.")
        return cls(line_id, sku, title, unit_price, quantity)

    def id(self) -> OrderLineId:
        """Возвращает идентичность внутренней сущности."""
        return self._id

    def currency(self) -> str:
        """Возвращает валюту снимка цены."""
        return self._unit_price.currency()

    def subtotal(self) -> Money:
        """Вычисляет стоимость строки из её снимка и количества."""
        return self._unit_price.multiply(self._quantity)

    def to_dict(self) -> dict:
        """Возвращает безопасный снимок строки для прикладного слоя."""
        return {"line_id": self._id.value(), "sku": self._sku.value(), "title": self._title, "quantity": self._quantity.value(), "unit_price": str(self._unit_price.amount()), "currency": self._unit_price.currency()}


class Order:
    """Корень агрегата, владеющий строками и правилами оформления заказа."""

    def __init__(self, order_id: OrderId, buyer_id: BuyerId, currency: str) -> None:
        """Создаёт черновик заказа через фабрику ``create_draft``.

        Args:
            order_id: Идентификатор корня.
            buyer_id: Ссылка по идентификатору на покупателя.
            currency: Единственная валюта будущих строк.
        """
        self._id = order_id
        self._buyer_id = buyer_id
        self._currency = Money("0", currency).currency()
        self._lines: list[OrderLine] = []
        self._address: str | None = None
        self._status = OrderStatus.DRAFT

    @classmethod
    def create_draft(cls, order_id: OrderId, buyer_id: BuyerId, currency: str) -> "Order":
        """Создаёт новый черновик как единственную точку рождения заказа.

        Args:
            order_id: Новый идентификатор корня.
            buyer_id: Идентификатор покупателя.
            currency: Валюта заказа.

        Returns:
            Новый заказ в состоянии ``DRAFT``.
        """
        return cls(order_id, buyer_id, currency)

    def add_paid_line(self, line: OrderLine) -> None:
        """Добавляет строку, сохраняя валюту и уникальность идентификатора.

        Args:
            line: Внутренняя сущность, созданная фабрикой строки.

        Raises:
            DomainRuleViolation: Заказ не черновик, валюта другая либо идентификатор повторён.
        """
        self._require_draft()
        if line.currency() != self._currency:
            raise DomainRuleViolation("Все строки заказа должны иметь одну валюту.")
        if any(existing.id() == line.id() for existing in self._lines):
            raise DomainRuleViolation("Идентификатор строки уже используется.")
        self._lines.append(line)

    def set_shipping_address(self, address: str) -> None:
        """Фиксирует адрес как часть черновика.

        Args:
            address: Непустой адрес доставки.

        Raises:
            DomainRuleViolation: Заказ не черновик или адрес пуст.
        """
        self._require_draft()
        if not isinstance(address, str) or not address.strip():
            raise DomainRuleViolation("Адрес доставки обязателен.")
        self._address = address.strip()

    def place(self) -> None:
        """Переводит полный черновик в размещённый заказ.

        Raises:
            DomainRuleViolation: Нет строк, адреса либо положительной суммы.
        """
        self._require_draft()
        if not self._lines:
            raise DomainRuleViolation("Нельзя разместить заказ без строк.")
        if self._address is None:
            raise DomainRuleViolation("Нельзя разместить заказ без адреса.")
        if self.total().amount() <= 0:
            raise DomainRuleViolation("Сумма заказа должна быть положительной.")
        self._status = OrderStatus.PLACED

    def cancel(self) -> None:
        """Отменяет черновик или размещённый заказ."""
        if self._status is OrderStatus.CANCELLED:
            raise DomainRuleViolation("Заказ уже отменён.")
        self._status = OrderStatus.CANCELLED

    def total(self) -> Money:
        """Возвращает итоговую сумму всех внутренних строк."""
        return Money(sum((line.subtotal().amount() for line in self._lines), Decimal("0")), self._currency)

    def id(self) -> OrderId:
        """Возвращает идентификатор корня."""
        return self._id

    def status(self) -> OrderStatus:
        """Возвращает текущее состояние жизненного цикла."""
        return self._status

    def snapshot(self) -> dict:
        """Возвращает прикладной снимок без выдачи изменяемой коллекции строк."""
        return {"id": self._id.value(), "buyer_id": self._buyer_id.value(), "currency": self._currency, "address": self._address, "status": self._status.value, "total": str(self.total().amount()), "lines": [line.to_dict() for line in self._lines]}

    def _require_draft(self) -> None:
        """Проверяет предусловие изменения состава черновика.

        Raises:
            DomainRuleViolation: Заказ уже размещён или отменён.
        """
        if self._status is not OrderStatus.DRAFT:
            raise DomainRuleViolation("Изменять можно только черновик заказа.")
