from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Currency:
    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or len(self.code.strip()) != 3:
            raise ValueError("Currency code must be a 3-letter string")
        object.__setattr__(self, "code", self.code.strip().upper())

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class Quantity:
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            raise ValueError("Quantity must be an int")
        if self.value <= 0:
            raise ValueError("Quantity must be positive")

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise ValueError("Money amount must be Decimal")
        if self.amount < 0:
            raise ValueError("Money amount must be non-negative")
        if not isinstance(self.currency, Currency):
            raise ValueError("Money currency must be Currency")

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Currency mismatch")
        return Money(self.amount + other.amount, self.currency)

    def __mul__(self, factor: int) -> "Money":
        if not isinstance(factor, int) or factor < 0:
            raise ValueError("factor must be non-negative int")
        return Money(self.amount * factor, self.currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


@dataclass(frozen=True)
class ShippingAddress:
    city: str
    street: str
    building: str
    postal_code: str

    def __post_init__(self) -> None:
        for field in ("city", "street", "building", "postal_code"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ShippingAddress.{field} must be non-empty")


@dataclass(frozen=True)
class StockBalance:
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            raise ValueError("StockBalance must be an int")
        if self.value < 0:
            raise ValueError("StockBalance cannot be negative")

    def increase(self, quantity: Quantity) -> "StockBalance":
        return StockBalance(self.value + quantity.value)

    def decrease(self, quantity: Quantity) -> "StockBalance":
        if quantity.value > self.value:
            raise ValueError("Insufficient physical stock")
        return StockBalance(self.value - quantity.value)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class ReservationDeadline:
    value: datetime

    def is_due(self, now: datetime) -> bool:
        return now >= self.value