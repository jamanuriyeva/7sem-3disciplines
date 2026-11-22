from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .identifiers import OrderId, ReservationId, Sku, StockItemId, WarehouseId
from .values import Quantity, ReservationDeadline, StockBalance


class StockError(Exception):
    """Base for domain errors of the StockItem aggregate."""


class InsufficientStockError(StockError):
    pass


class DuplicateReservationError(StockError):
    pass


class ReservationNotFoundError(StockError):
    pass


class ReservationStatus(Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class StockReservation:
    def __init__(
        self,
        reservation_id: ReservationId,
        order_id: OrderId,
        quantity: Quantity,
        deadline: ReservationDeadline,
    ) -> None:
        self._id = reservation_id
        self._order_id = order_id
        self._quantity = quantity
        self._deadline = deadline
        self._status = ReservationStatus.ACTIVE

    def _release(self) -> None:
        if self._status is ReservationStatus.ACTIVE:
            self._status = ReservationStatus.RELEASED

    def _expire_if_due(self, now: datetime) -> bool:
        if self._status is ReservationStatus.ACTIVE and self._deadline.is_due(now):
            self._status = ReservationStatus.EXPIRED
            return True
        return False

    def _summary(self) -> "ReservationSummary":
        return ReservationSummary(
            reservation_id=self._id,
            order_id=self._order_id,
            quantity=self._quantity,
            deadline=self._deadline,
            status=self._status,
        )


@dataclass(frozen=True)
class ReservationSummary:
    reservation_id: ReservationId
    order_id: OrderId
    quantity: Quantity
    deadline: ReservationDeadline
    status: ReservationStatus


class StockItem:
    def __init__(
        self,
        stock_item_id: StockItemId,
        warehouse_id: WarehouseId,
        sku: Sku,
        physical_balance: StockBalance,
    ) -> None:
        self._id = stock_item_id
        self._warehouse_id = warehouse_id
        self._sku = sku
        self._physical_balance = physical_balance
        self._reservations: list[StockReservation] = []

    def id(self) -> StockItemId:
        return self._id

    def physical_balance(self) -> StockBalance:
        return self._physical_balance

    def reservations(self) -> tuple[ReservationSummary, ...]:
        return tuple(r._summary() for r in self._reservations)

    def available_quantity(self) -> StockBalance:
        active = sum(
            r._summary().quantity.value
            for r in self._reservations
            if r._summary().status is ReservationStatus.ACTIVE
        )
        return StockBalance(self._physical_balance.value - active)

    def receive(self, quantity: Quantity) -> None:
        self._physical_balance = self._physical_balance.increase(quantity)

    def write_off(self, quantity: Quantity) -> None:
        if quantity.value > self.available_quantity().value:
            raise InsufficientStockError("Not enough available stock to write off")
        self._physical_balance = self._physical_balance.decrease(quantity)

    def reserve(
        self,
        reservation_id: ReservationId,
        order_id: OrderId,
        quantity: Quantity,
        deadline: ReservationDeadline,
    ) -> None:
        if any(r._summary().reservation_id == reservation_id for r in self._reservations):
            raise DuplicateReservationError(f"Reservation already exists: {reservation_id}")
        if quantity.value > self.available_quantity().value:
            raise InsufficientStockError("Not enough available stock to reserve")
        self._reservations.append(
            StockReservation(reservation_id, order_id, quantity, deadline)
        )

    def release_reservation(self, reservation_id: ReservationId) -> None:
        reservation = self._find_reservation(reservation_id)
        if reservation._summary().status is ReservationStatus.ACTIVE:
            reservation._release()
        else:
            raise StockError("Reservation is not active")

    def expire_reservations(self, now: datetime) -> int:
        count = 0
        for reservation in self._reservations:
            if reservation._expire_if_due(now):
                count += 1
        return count

    def _find_reservation(self, reservation_id: ReservationId) -> StockReservation:
        for reservation in self._reservations:
            if reservation._summary().reservation_id == reservation_id:
                return reservation
        raise ReservationNotFoundError(f"Reservation not found: {reservation_id}")