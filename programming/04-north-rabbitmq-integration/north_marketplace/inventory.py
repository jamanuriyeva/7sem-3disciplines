"""Контекст Inventory: физический остаток и локальные резервы."""

from __future__ import annotations

from .events import InventoryReservationRejected, InventoryReservationReleased, InventoryReserved, RaisesDomainEvents
from .kernel import DomainRuleViolation, Identifier, OrderLineReference, OrderReference, Quantity, Sku, WarehouseId


class StockItemId(Identifier):
    """Идентификатор корня складской позиции."""


class ReservationId(Identifier):
    """Идентификатор внутреннего резерва."""


class StockReservation:
    """Внутренняя сущность, связывающая количество с OrderId как значением."""

    def __init__(self, reservation_id: ReservationId, order_id: OrderReference, line_id: OrderLineReference, quantity: Quantity, active: bool = True) -> None:
        """Создаёт резерв без навигации к агрегату заказа."""
        self._id, self._order_id, self._line_id, self._quantity, self._active = reservation_id, order_id, line_id, quantity, active

    def active_quantity(self) -> int:
        """Возвращает количество только активного резерва."""
        return self._quantity.value() if self._active else 0

    def release(self) -> None:
        """Освобождает резерв, не удаляя его из истории агрегата."""
        self._active = False

    def to_state(self) -> dict:
        """Сериализует внутреннюю сущность."""
        return {"id": self._id.value(), "order_id": self._order_id.value(), "line_id": self._line_id.value(), "quantity": self._quantity.value(), "active": self._active}

    @classmethod
    def from_state(cls, state: dict) -> "StockReservation":
        """Восстанавливает внутренний резерв."""
        return cls(ReservationId(state["id"]), OrderReference(state["order_id"]), OrderLineReference(state.get("line_id", "unknown-line")), Quantity(state["quantity"]), bool(state["active"]))


class StockItem(RaisesDomainEvents):
    """Корень складской позиции, владеющий физическим остатком и резервами."""

    def __init__(self, item_id: StockItemId, warehouse_id: WarehouseId, sku: Sku, physical: int = 0, reservations: list[StockReservation] | None = None) -> None:
        """Создаёт или восстанавливает позицию склада."""
        self._start_event_recording()
        self._id, self._warehouse_id, self._sku = item_id, warehouse_id, sku
        self._physical, self._reservations = physical, list(reservations or [])

    @classmethod
    def create(cls, item_id: StockItemId, warehouse_id: WarehouseId, sku: Sku) -> "StockItem":
        """Создаёт пустую складскую позицию."""
        return cls(item_id, warehouse_id, sku)

    def receive(self, quantity: Quantity) -> None:
        """Увеличивает физический остаток."""
        self._physical += quantity.value()

    def reserve(self, reservation_id: ReservationId, order_id: OrderReference, line_id: OrderLineReference, quantity: Quantity) -> bool:
        """Создаёт активный резерв после проверки доступного остатка.

        Raises:
            DomainRuleViolation: Резерв повторяется либо остатка недостаточно.
        """
        if any(item.to_state()["id"] == reservation_id.value() for item in self._reservations):
            raise DomainRuleViolation("Идентификатор резерва уже существует.")
        if self.available_quantity() < quantity.value():
            self._record(InventoryReservationRejected({"stock_item_id": self._id.value(), "reservation_id": reservation_id.value(), "order_id": order_id.value(), "line_id": line_id.value(), "reason": "Недостаточный остаток"}))
            return False
        self._reservations.append(StockReservation(reservation_id, order_id, line_id, quantity))
        self._record(InventoryReserved({"stock_item_id": self._id.value(), "reservation_id": reservation_id.value(), "order_id": order_id.value(), "line_id": line_id.value(), "quantity": quantity.value()}))
        return True

    def release(self, reservation_id: ReservationId) -> None:
        """Освобождает один ранее созданный резерв.

        Raises:
            DomainRuleViolation: Резерв не найден.
        """
        for reservation in self._reservations:
            if reservation.to_state()["id"] == reservation_id.value():
                reservation.release()
                self._record(InventoryReservationReleased({"stock_item_id": self._id.value(), "reservation_id": reservation_id.value(), "order_id": reservation.to_state()["order_id"]}))
                return
        raise DomainRuleViolation("Резерв не найден.")

    def write_off(self, quantity: Quantity) -> None:
        """Списывает товар, не затрагивая уже активные резервы."""
        if self._physical - quantity.value() < self._active_reserved():
            raise DomainRuleViolation("Списание пересекает активные резервы.")
        self._physical -= quantity.value()

    def available_quantity(self) -> int:
        """Возвращает физический остаток за вычетом активных резервов."""
        return self._physical - self._active_reserved()

    def id(self) -> StockItemId:
        """Возвращает идентификатор корня."""
        return self._id

    def to_state(self) -> dict:
        """Подготавливает актуальное состояние для Data Mapper."""
        return {"id": self._id.value(), "warehouse_id": self._warehouse_id.value(), "sku": self._sku.value(), "physical": self._physical, "reservations": [item.to_state() for item in self._reservations]}

    @classmethod
    def from_state(cls, state: dict) -> "StockItem":
        """Восстанавливает корень и внутренние резервы."""
        return cls(StockItemId(state["id"]), WarehouseId(state["warehouse_id"]), Sku(state["sku"]), int(state["physical"]), [StockReservation.from_state(item) for item in state["reservations"]])

    def _active_reserved(self) -> int:
        """Подсчитывает только активные резервы."""
        return sum(item.active_quantity() for item in self._reservations)


class AllocationPlan:
    """Неизменяемый результат выбора позиции и количества для заказа."""

    def __init__(self, stock_item_id: str, warehouse_id: str, sku: str, quantity: int) -> None:
        """Создаёт результат доменной службы."""
        self.stock_item_id, self.warehouse_id, self.sku, self.quantity = stock_item_id, warehouse_id, sku, quantity


class WarehouseAllocationService:
    """Выбирает складскую позицию по данным нескольких корней без их изменения."""

    def allocate(self, candidates: list[StockItem], quantity: Quantity) -> AllocationPlan:
        """Строит план для первой позиции с достаточным доступным остатком.

        Args:
            candidates: Позиции, выбранные прикладным слоем по SKU.
            quantity: Требуемое количество.

        Returns:
            План последующего изменения одного корня.

        Raises:
            DomainRuleViolation: Ни одна позиция не может покрыть запрос.
        """
        for item in candidates:
            if item.available_quantity() >= quantity.value():
                state = item.to_state()
                return AllocationPlan(state["id"], state["warehouse_id"], state["sku"], quantity.value())
        raise DomainRuleViolation("Подходящего складского остатка нет.")
