"""Сохраняемая модель длительного процесса обеспечения заказа.

``FulfillmentProcess`` не является агрегатом одного из пяти контекстов. Он
хранит состояние координации и выдаёт локальные команды. Надёжное выполнение
этих команд обеспечивает отдельная таблица work-item'ов; процесс не вызывает
корни агрегатов напрямую.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from .events import Notification
from .kernel import OrderReference, PaymentReference, ShipmentReference


class FulfillmentStage(Enum):
    """Этапы процесса от размещения заказа до итогового решения."""

    WAITING_PAYMENT = "WAITING_PAYMENT"
    WAITING_STOCK = "WAITING_STOCK"
    WAITING_SHIPMENT = "WAITING_SHIPMENT"
    WAITING_CAPTURE = "WAITING_CAPTURE"
    COMPENSATING_SHIPMENT = "COMPENSATING_SHIPMENT"
    COMPENSATING_STOCK = "COMPENSATING_STOCK"
    COMPENSATING_PAYMENT = "COMPENSATING_PAYMENT"
    WAITING_ORDER_RESULT = "WAITING_ORDER_RESULT"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class FulfillmentProcess:
    """Process manager одного заказа со ссылками только по идентификаторам.

    После уведомления объект меняет свой этап и возвращает описание одной
    следующей локальной команды. Описание сохраняется вместе с этапом и
    receipt уведомления, поэтому остановка между реакцией и вызовом целевого
    агрегата не теряет работу.
    """

    def __init__(self, order_id: OrderReference, amount: str, currency: str, order_state: dict, stage: FulfillmentStage = FulfillmentStage.WAITING_PAYMENT, payment_id: PaymentReference | None = None, shipment_id: ShipmentReference | None = None, reservations: list[dict] | None = None, next_line_index: int = 0, rejection_reason: str | None = None) -> None:
        """Создаёт или восстанавливает состояние одного процесса."""
        self.order_id = order_id
        self.amount = amount
        self.currency = currency
        self.order_state = dict(order_state)
        self.stage = stage
        self.payment_id = payment_id or PaymentReference(f"payment-{order_id.value()}")
        self.shipment_id = shipment_id or ShipmentReference(f"shipment-{order_id.value()}")
        self.reservations = list(reservations or [])
        self.next_line_index = next_line_index
        self.rejection_reason = rejection_reason

    @classmethod
    def start(cls, order_state: dict) -> "FulfillmentProcess":
        """Создаёт процесс из опубликованного состояния размещённого заказа."""
        amount = sum((Decimal(line["amount"]) * int(line["quantity"]) for line in order_state["lines"]), Decimal("0"))
        return cls(OrderReference(order_state["id"]), str(amount), order_state["currency"], order_state)

    def first_command(self) -> dict:
        """Возвращает первую команду авторизации после ``OrderPlaced``."""
        return {"name": "AUTHORIZE_PAYMENT"}

    def react(self, notification: Notification) -> dict | None:
        """Меняет этап по одному факту и возвращает следующую команду.

        Неподходящий для текущего этапа факт считается уже обработанным другой
        ветвью процесса и не порождает вторую команду.
        """
        event_type = notification.event_type
        payload = notification.payload
        if event_type == "PaymentAuthorized" and self.stage is FulfillmentStage.WAITING_PAYMENT:
            self.stage = FulfillmentStage.WAITING_STOCK
            return self._next_reservation_command()
        if event_type == "PaymentAuthorizationRejected" and self.stage is FulfillmentStage.WAITING_PAYMENT:
            self.rejection_reason = payload.get("reason", "Авторизация платежа отклонена")
            self.stage = FulfillmentStage.WAITING_ORDER_RESULT
            return {"name": "REJECT_ORDER"}
        if event_type == "InventoryReserved" and self.stage is FulfillmentStage.WAITING_STOCK:
            self.reservations.append({"reservation_id": payload["reservation_id"], "stock_item_id": payload["stock_item_id"]})
            self.next_line_index += 1
            command = self._next_reservation_command()
            if command is not None:
                return command
            self.stage = FulfillmentStage.WAITING_SHIPMENT
            return {"name": "CREATE_SHIPMENT"}
        if event_type == "InventoryReservationRejected" and self.stage is FulfillmentStage.WAITING_STOCK:
            self.rejection_reason = payload.get("reason", "Недостаточный остаток")
            self.stage = FulfillmentStage.COMPENSATING_STOCK
            return self._next_release_command() or self._begin_payment_compensation()
        if event_type == "ShipmentCreated" and self.stage is FulfillmentStage.WAITING_SHIPMENT:
            self.stage = FulfillmentStage.WAITING_CAPTURE
            return {"name": "CAPTURE_PAYMENT"}
        if event_type == "PaymentCaptureRejected" and self.stage is FulfillmentStage.WAITING_CAPTURE:
            self.rejection_reason = payload.get("reason", "Захват платежа отклонён")
            self.stage = FulfillmentStage.COMPENSATING_SHIPMENT
            return {"name": "CANCEL_SHIPMENT"}
        if event_type == "ShipmentCancelled" and self.stage is FulfillmentStage.COMPENSATING_SHIPMENT:
            self.stage = FulfillmentStage.COMPENSATING_STOCK
            return self._next_release_command() or self._begin_payment_compensation()
        if event_type == "InventoryReservationReleased" and self.stage is FulfillmentStage.COMPENSATING_STOCK:
            self.reservations = [item for item in self.reservations if item["reservation_id"] != payload["reservation_id"]]
            return self._next_release_command() or self._begin_payment_compensation()
        if event_type == "PaymentCaptured" and self.stage is FulfillmentStage.WAITING_CAPTURE:
            self.stage = FulfillmentStage.WAITING_ORDER_RESULT
            return {"name": "FULFILL_ORDER"}
        if event_type == "PaymentVoided" and self.stage is FulfillmentStage.COMPENSATING_PAYMENT:
            self.stage = FulfillmentStage.WAITING_ORDER_RESULT
            return {"name": "REJECT_ORDER"}
        if event_type == "OrderFulfilled" and self.stage is FulfillmentStage.WAITING_ORDER_RESULT:
            self.stage = FulfillmentStage.COMPLETED
        if event_type == "OrderFulfillmentRejected" and self.stage is FulfillmentStage.WAITING_ORDER_RESULT:
            self.stage = FulfillmentStage.REJECTED
        return None

    def _next_reservation_command(self) -> dict | None:
        """Строит команду резерва первой ещё не подтверждённой строки."""
        lines = self.order_state["lines"]
        if self.next_line_index >= len(lines):
            return None
        line = lines[self.next_line_index]
        return {"name": "RESERVE_LINE", "line_id": line["line_id"], "sku": line["sku"], "quantity": line["quantity"], "reservation_id": f"reservation-{self.order_id.value()}-{line['line_id']}"}

    def _next_release_command(self) -> dict | None:
        """Строит команду освобождения одного подтверждённого резерва."""
        if not self.reservations:
            return None
        return {"name": "RELEASE_RESERVATION", **self.reservations[0]}

    def _begin_payment_compensation(self) -> dict:
        """Переводит процесс к аннулированию ещё не захваченного платежа."""
        self.stage = FulfillmentStage.COMPENSATING_PAYMENT
        return {"name": "VOID_PAYMENT"}

    def to_state(self) -> dict:
        """Возвращает сериализуемое состояние process manager."""
        return {"id": self.order_id.value(), "order_id": self.order_id.value(), "amount": self.amount, "currency": self.currency, "order_state": self.order_state, "stage": self.stage.value, "payment_id": self.payment_id.value(), "shipment_id": self.shipment_id.value(), "reservations": self.reservations, "next_line_index": self.next_line_index, "rejection_reason": self.rejection_reason}

    @classmethod
    def from_state(cls, state: dict) -> "FulfillmentProcess":
        """Восстанавливает процесс без создания новой команды."""
        return cls(OrderReference(state["order_id"]), state["amount"], state["currency"], state["order_state"], FulfillmentStage(state["stage"]), PaymentReference(state["payment_id"]), ShipmentReference(state["shipment_id"]), state["reservations"], int(state["next_line_index"]), state["rejection_reason"])

