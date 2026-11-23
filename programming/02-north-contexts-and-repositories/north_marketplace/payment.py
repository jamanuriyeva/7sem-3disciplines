"""Контекст Payment: авторизация, захват и аннулирование оплаты."""

from __future__ import annotations

from enum import Enum

from .kernel import DomainRuleViolation, Identifier, Money, OrderReference


class PaymentId(Identifier):
    """Идентификатор корня платежа."""


class PaymentStatus(Enum):
    """Допустимые состояния учебного платежа."""

    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    REFUSED = "REFUSED"
    CAPTURED = "CAPTURED"
    VOIDED = "VOIDED"


class Payment:
    """Корень платежа, не знающий агрегат Order, а хранящий только его идентификатор."""

    def __init__(self, payment_id: PaymentId, order_id: OrderReference, amount: Money, status: PaymentStatus = PaymentStatus.PENDING) -> None:
        """Создаёт или восстанавливает платёж."""
        self._id, self._order_id, self._amount, self._status = payment_id, order_id, amount, status

    @classmethod
    def request_authorization(cls, payment_id: PaymentId, order_id: OrderReference, amount: Money) -> "Payment":
        """Создаёт ожидающий авторизации платёж.

        Raises:
            DomainRuleViolation: Сумма не положительна.
        """
        if amount.amount() <= 0:
            raise DomainRuleViolation("Нельзя авторизовать нулевой платёж.")
        return cls(payment_id, order_id, amount)

    def authorize(self) -> None:
        """Подтверждает авторизацию ожидающего платежа."""
        self._move(PaymentStatus.PENDING, PaymentStatus.AUTHORIZED)

    def refuse(self) -> None:
        """Фиксирует отказ провайдера для ожидающего платежа."""
        self._move(PaymentStatus.PENDING, PaymentStatus.REFUSED)

    def capture(self) -> None:
        """Захватывает ранее авторизованный платёж."""
        self._move(PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED)

    def void(self) -> None:
        """Аннулирует авторизацию как компенсирующее действие."""
        self._move(PaymentStatus.AUTHORIZED, PaymentStatus.VOIDED)

    def id(self) -> PaymentId:
        """Возвращает идентификатор корня."""
        return self._id

    def to_state(self) -> dict:
        """Подготавливает актуальное состояние для Data Mapper."""
        return {"id": self._id.value(), "order_id": self._order_id.value(), "amount": str(self._amount.amount()), "currency": self._amount.currency(), "status": self._status.value}

    @classmethod
    def from_state(cls, state: dict) -> "Payment":
        """Восстанавливает платёж из состояния хранения."""
        return cls(PaymentId(state["id"]), OrderReference(state["order_id"]), Money(state["amount"], state["currency"]), PaymentStatus(state["status"]))

    def _move(self, expected: PaymentStatus, target: PaymentStatus) -> None:
        """Проверяет точный переход жизненного цикла.

        Raises:
            DomainRuleViolation: Текущее состояние несовместимо с командой.
        """
        if self._status is not expected:
            raise DomainRuleViolation("Переход платежа недопустим в текущем состоянии.")
        self._status = target
