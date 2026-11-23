"""Контекст Shipping: создание и диспетчеризация отправлений."""

from __future__ import annotations

from enum import Enum

from .events import RaisesDomainEvents, ShipmentCancelled, ShipmentCreated, ShipmentDispatched
from .kernel import DomainRuleViolation, Identifier, OrderReference


class ShipmentId(Identifier):
    """Идентификатор корня отправления."""


class ShipmentStatus(Enum):
    """Состояния отправления после получения состава заказа."""

    CREATED = "CREATED"
    DISPATCHED = "DISPATCHED"
    CANCELLED = "CANCELLED"


class Shipment(RaisesDomainEvents):
    """Корень отправления со снимком адреса и состава без ссылки на Order."""

    def __init__(self, shipment_id: ShipmentId, order_id: OrderReference, address: str, lines: list[dict], status: ShipmentStatus = ShipmentStatus.CREATED) -> None:
        """Создаёт либо восстанавливает отправление."""
        self._start_event_recording()
        self._id, self._order_id, self._address, self._lines, self._status = shipment_id, order_id, address, list(lines), status

    @classmethod
    def create(cls, shipment_id: ShipmentId, order_id: OrderReference, address: str, lines: list[dict]) -> "Shipment":
        """Создаёт отправление только с полным адресом и составом."""
        if not address.strip() or not lines:
            raise DomainRuleViolation("Отправлению нужны адрес и состав.")
        shipment = cls(shipment_id, order_id, address.strip(), lines)
        shipment._record(ShipmentCreated({"shipment_id": shipment_id.value(), "order_id": order_id.value(), "address": address.strip(), "lines": list(lines)}))
        return shipment

    def dispatch(self) -> None:
        """Передаёт созданное отправление перевозчику."""
        if self._status is not ShipmentStatus.CREATED:
            raise DomainRuleViolation("Передать можно только созданное отправление.")
        self._status = ShipmentStatus.DISPATCHED
        self._record(ShipmentDispatched({"shipment_id": self._id.value(), "order_id": self._order_id.value()}))

    def cancel(self) -> None:
        """Отменяет ещё не переданное отправление как компенсацию."""
        if self._status is not ShipmentStatus.CREATED:
            raise DomainRuleViolation("Отменить можно только созданное отправление.")
        self._status = ShipmentStatus.CANCELLED
        self._record(ShipmentCancelled({"shipment_id": self._id.value(), "order_id": self._order_id.value()}))

    def id(self) -> ShipmentId:
        """Возвращает идентификатор корня."""
        return self._id

    def to_state(self) -> dict:
        """Подготавливает актуальное состояние для Data Mapper."""
        return {"id": self._id.value(), "order_id": self._order_id.value(), "address": self._address, "lines": self._lines, "status": self._status.value}

    @classmethod
    def from_state(cls, state: dict) -> "Shipment":
        """Восстанавливает корень отправления."""
        return cls(ShipmentId(state["id"]), OrderReference(state["order_id"]), state["address"], state["lines"], ShipmentStatus(state["status"]))


class DispatchPlan:
    """Результат выбора готовых отправлений для одной передачи перевозчику."""

    def __init__(self, shipment_ids: list[str]) -> None:
        """Создаёт неизменяемую по смыслу последовательность идентификаторов."""
        self.shipment_ids = tuple(shipment_ids)


class ShipmentPlanningService:
    """Строит план диспетчеризации без изменения нескольких отправлений."""

    def plan(self, shipments: list[Shipment]) -> DispatchPlan:
        """Отбирает отправления в состоянии CREATED.

        Returns:
            План идентификаторов для последовательных команд приложения.
        """
        return DispatchPlan([shipment.id().value() for shipment in shipments if shipment.to_state()["status"] == ShipmentStatus.CREATED.value])
