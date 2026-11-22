from __future__ import annotations

from dataclasses import dataclass


def _require_non_empty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True)
class OrderId:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "OrderId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class OrderLineId:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "OrderLineId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class BuyerId:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "BuyerId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SellerId:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "SellerId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Sku:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "Sku")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class StockItemId:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "StockItemId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class WarehouseId:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "WarehouseId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ReservationId:
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.value, "ReservationId")

    def __str__(self) -> str:
        return self.value