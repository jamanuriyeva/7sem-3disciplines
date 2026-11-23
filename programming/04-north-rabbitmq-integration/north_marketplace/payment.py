"""Контекст Payment: авторизация, захват и аннулирование оплаты."""

from __future__ import annotations

from enum import Enum

from .events import PaymentAuthorizationRejected, PaymentAuthorized, PaymentCaptureRejected, PaymentCaptured, PaymentVoided, RaisesDomainEvents
from .kernel import DomainRuleViolation, Identifier, Money, OrderReference


class PaymentId(Identifier):
    """Идентификатор корня платежа."""


class PaymentStatus(Enum):
    """Допустимые состояния учебного платежа."""

    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    CAPTURED = "CAPTURED"
    CAPTURE_REJECTED = "CAPTURE_REJECTED"
    VOIDED = "VOIDED"


class Payment(RaisesDomainEvents):
    """Корень платежа, не знающий агрегат Order, а хранящий только его идентификатор."""

    def __init__(self, payment_id: PaymentId, order_id: OrderReference, amount: Money, status: PaymentStatus = PaymentStatus.PENDING) -> None:
        """Создаёт или восстанавливает платёж."""
        self._start_event_recording()
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
        self._record(PaymentAuthorized({"payment_id": self._id.value(), "order_id": self._order_id.value(), "amount": str(self._amount.amount()), "currency": self._amount.currency()}))

    def reject_authorization(self, reason: str) -> None:
        """Фиксирует отказ внешней авторизации как факт корня."""
        self._move(PaymentStatus.PENDING, PaymentStatus.AUTHORIZATION_REJECTED)
        self._record(PaymentAuthorizationRejected({"payment_id": self._id.value(), "order_id": self._order_id.value(), "reason": reason}))

    def capture(self) -> None:
        """Захватывает ранее авторизованный платёж."""
        self._move(PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED)
        self._record(PaymentCaptured({"payment_id": self._id.value(), "order_id": self._order_id.value()}))

    def reject_capture(self, reason: str) -> None:
        """Фиксирует отказ захвата, сохраняя возможность аннулирования."""
        self._move(PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURE_REJECTED)
        self._record(PaymentCaptureRejected({"payment_id": self._id.value(), "order_id": self._order_id.value(), "reason": reason}))

    def void(self) -> None:
        """Аннулирует авторизацию как компенсирующее действие."""
        if self._status not in {PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURE_REJECTED}:
            raise DomainRuleViolation("Аннулировать можно только авторизованный или отклонённый при захвате платёж.")
        self._status = PaymentStatus.VOIDED
        self._record(PaymentVoided({"payment_id": self._id.value(), "order_id": self._order_id.value()}))

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
