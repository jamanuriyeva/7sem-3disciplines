"""Прикладная служба и диспетчер устойчивых команд обеспечения заказа."""

from __future__ import annotations

from .application import InventoryApplicationService, OrderingApplicationService, PaymentApplicationService, ShippingApplicationService
from .events import Notification, NotificationDispatcher, NotificationLog
from .fulfillment import FulfillmentProcess
from .ports import NotificationReader, UnitOfWorkFactory


class FulfillmentApplicationService:
    """Реагирует на факты и направляет команды в локальные прикладные службы.

    Служба не меняет чужой агрегат в транзакции обработчика уведомления. Она
    сохраняет состояние процесса, receipt и work-item одной транзакцией, а
    затем отдельно исполняет одну захваченную команду. Это делает повтор после
    остановки безопасным и наблюдаемым.
    """

    subscriber_name = "FulfillmentApplicationService"
    subscribed_events = {
        "OrderPlaced", "PaymentAuthorized", "PaymentAuthorizationRejected",
        "InventoryReserved", "InventoryReservationRejected",
        "InventoryReservationReleased", "ShipmentCreated", "ShipmentCancelled",
        "PaymentCaptured", "PaymentCaptureRejected", "PaymentVoided",
        "OrderFulfilled", "OrderFulfillmentRejected",
    }

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, notification_reader: NotificationReader) -> None:
        """Создаёт прикладные службы контекстов поверх портов хранения.

        Args:
            unit_of_work_factory: Фабрика локальных транзакций.
            notification_reader: Явный читатель диагностических polling-фактов.
        """
        self._unit_of_work_factory = unit_of_work_factory
        self._notification_reader = notification_reader
        self._ordering = OrderingApplicationService(unit_of_work_factory)
        self._inventory = InventoryApplicationService(unit_of_work_factory)
        self._payment = PaymentApplicationService(unit_of_work_factory)
        self._shipping = ShippingApplicationService(unit_of_work_factory)

    def dispatcher(self) -> NotificationDispatcher:
        """Возвращает polling-диспетчер устойчивой подписки процесса."""
        return NotificationDispatcher(NotificationLog(self._notification_reader), self.subscriber_name, self.subscribed_events, self.handle)

    def handle(self, notification: Notification) -> None:
        """Фиксирует реакцию процесса, receipt и будущую команду атомарно."""
        order_id = notification.payload.get("order_id", notification.aggregate_id)
        with self._unit_of_work_factory.unit_of_work() as uow:
            if uow.is_handled(self.subscriber_name, notification.position):
                return
            processes = uow.processes()
            process = processes.get(order_id)
            if process is None:
                if notification.event_type != "OrderPlaced":
                    uow.mark_handled(self.subscriber_name, notification.position)
                    return
                process = FulfillmentProcess.start(notification.payload)
                command = process.first_command()
            else:
                command = process.react(notification)
            processes.save(process)
            if command is not None:
                command_id = self._command_id(order_id, notification.event_id, command, process.next_line_index)
                uow.process_commands().enqueue(command_id, order_id, command)
            uow.mark_handled(self.subscriber_name, notification.position)

    def dispatch_commands(self, limit: int = 100) -> int:
        """Исполняет ограниченную порцию безопасно захваченных work-item'ов."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            commands = uow.process_commands().claim(limit)
        completed = 0
        for command in commands:
            try:
                self._execute(command.process_id, command.command, command.command_id)
            except Exception:
                with self._unit_of_work_factory.unit_of_work() as uow:
                    uow.process_commands().release(command.command_id, command.lease_token)
                raise
            with self._unit_of_work_factory.unit_of_work() as uow:
                uow.process_commands().complete(command.command_id, command.lease_token)
            completed += 1
        return completed

    def resume(self, limit: int = 100) -> int:
        """Сначала принимает факты, затем продолжает пережившие остановку команды."""
        delivered = self.dispatcher().dispatch(limit)
        executed = self.dispatch_commands(max(limit - delivered, 0))
        return delivered + executed

    def state(self, order_id: str) -> dict | None:
        """Возвращает текущее состояние процесса для CLI и HTTP."""
        with self._unit_of_work_factory.unit_of_work() as uow:
            process = uow.processes().get(order_id)
            return None if process is None else process.to_state()

    def _execute(self, order_id: str, command: dict, causation_id: str) -> None:
        """Маршрутизирует одну устойчивую команду в локальный контекст."""
        name = command["name"]
        process = self.state(order_id)
        if process is None:
            raise KeyError("Процесс обеспечения не найден.")
        if name == "AUTHORIZE_PAYMENT":
            self._payment.authorize(process["payment_id"], order_id, process["amount"], process["currency"], causation_id)
        elif name == "RESERVE_LINE":
            self._inventory.reserve_for_line(order_id, command["line_id"], command["sku"], command["reservation_id"], int(command["quantity"]), causation_id)
        elif name == "CREATE_SHIPMENT":
            self._shipping.create(process["shipment_id"], process["order_state"], causation_id)
        elif name == "CAPTURE_PAYMENT":
            self._payment.capture(process["payment_id"], order_id, causation_id)
        elif name == "CANCEL_SHIPMENT":
            self._shipping.cancel(process["shipment_id"], order_id, causation_id)
        elif name == "RELEASE_RESERVATION":
            self._inventory.release_reservation(command["stock_item_id"], command["reservation_id"], order_id, causation_id)
        elif name == "VOID_PAYMENT":
            self._payment.void(process["payment_id"], order_id, causation_id)
        elif name == "FULFILL_ORDER":
            self._ordering.fulfill(order_id, causation_id)
        elif name == "REJECT_ORDER":
            self._ordering.reject_fulfillment(order_id, process["rejection_reason"] or "Обеспечение отклонено", causation_id)
        else:
            raise ValueError(f"Неизвестная команда процесса: {name}")

    @staticmethod
    def _command_id(order_id: str, event_id: str, command: dict, line_index: int) -> str:
        """Строит детерминированный ключ команды для повторной доставки факта."""
        return f"{order_id}:{event_id}:{command['name']}:{line_index}"
