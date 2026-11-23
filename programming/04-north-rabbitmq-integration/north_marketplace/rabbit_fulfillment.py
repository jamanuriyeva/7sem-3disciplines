"""RabbitMQ-вход для сохраняемого процесса обеспечения заказа."""

from __future__ import annotations

from .fulfillment import FulfillmentProcess
from .fulfillment_application import FulfillmentApplicationService
from .ports import ApplicationUnitOfWork, NotificationReader, UnitOfWorkFactory
from .rabbitmq import NotificationMessage


class RabbitFulfillmentApplicationService(FulfillmentApplicationService):
    """Переводит транспортный контракт в локальное решение process manager.

    Класс не создаёт события за агрегаты. Он сохраняет лишь своё состояние;
    последующая команда направляется обычной прикладной службе контекста.

    Note:
        Этот вариант сохраняет процесс и предметные команды polling-стадии.
        Он заменяет только способ приёма плоского уведомления: RabbitMQ
        доставляет транспортный контракт, а inbox защищает локальную реакцию
        от повтора.
    """

    subscriber_name = "RabbitFulfillmentApplicationService"
    event_binding_keys = (
        "ordering.OrderPlaced",
        "ordering.OrderFulfilled",
        "ordering.OrderFulfillmentRejected",
        "payment.PaymentAuthorized",
        "payment.PaymentAuthorizationRejected",
        "payment.PaymentCaptured",
        "payment.PaymentCaptureRejected",
        "payment.PaymentVoided",
        "inventory.InventoryReserved",
        "inventory.InventoryReservationRejected",
        "inventory.InventoryReservationReleased",
        "shipping.ShipmentCreated",
        "shipping.ShipmentCancelled",
    )

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, notification_reader: NotificationReader) -> None:
        """Получает явные порты состояния процесса и чтения фактов."""
        super().__init__(unit_of_work_factory, notification_reader)

    def accept_message(self, message: NotificationMessage, uow: ApplicationUnitOfWork) -> None:
        """Сохраняет реакцию процесса в транзакции входящего consumer.

        Args:
            message: Проверенный скалярный контракт RabbitMQ.
            uow: Текущая локальная транзакция consumer; inbox-receipt будет
                добавлен потребителем до её commit.
        """
        notification = message.notification()
        order_id = notification.payload.get("order_id", notification.aggregate_id)
        processes = uow.processes()
        process = processes.get(order_id)
        if process is None:
            if notification.event_type != "OrderPlaced":
                return
            process = FulfillmentProcess.start(notification.payload)
            command = process.first_command()
        else:
            command = process.react(notification)
        processes.save(process)
        if command is not None:
            command_id = self._command_id(order_id, notification.event_id, command, process.next_line_index)
            uow.process_commands().enqueue(command_id, order_id, command)

    def after_message_commit(self, message: NotificationMessage) -> None:
        """Запускает сохранённую команду после commit и manual ack.

        Если процесс остановится перед этим вызовом, work-item останется в
        таблице и будет захвачен обычной командой ``resume``.
        """
        order_id = message.payload.get("order_id", message.aggregate_id)
        self.dispatch_commands(1)
