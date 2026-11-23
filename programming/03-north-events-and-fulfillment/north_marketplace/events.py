"""Доменные факты, уведомления и polling-доставка третьей стадии.

Событие возникает внутри своего агрегата после успешной предметной команды.
``Notification`` создаётся только из уже сохранённой строки общего журнала и
передаёт между контекстами скалярные данные, а не объект агрегата.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable


class DomainEvent:
    """Прошедший факт предметной области без технической позиции журнала."""

    event_type = "DomainEvent"

    def __init__(self, payload: dict, occurred_on: str | None = None) -> None:
        """Создаёт неизменяемое по соглашению предметное содержание.

        Args:
            payload: Скалярные данные, достаточные подписчику для локальной команды.
            occurred_on: Время факта; передаётся только при восстановлении теста.
        """
        self._payload = dict(payload)
        self._occurred_on = occurred_on or datetime.now(UTC).isoformat()

    def payload(self) -> dict:
        """Возвращает копию полезной нагрузки факта."""
        return dict(self._payload)

    def occurred_on(self) -> str:
        """Возвращает время возникновения факта."""
        return self._occurred_on


class OrderPlaced(DomainEvent):
    """Факт успешного размещения заказа в Ordering."""

    event_type = "OrderPlaced"


class OrderFulfilled(DomainEvent):
    """Факт завершённого обеспечения заказа."""

    event_type = "OrderFulfilled"


class OrderFulfillmentRejected(DomainEvent):
    """Факт итогового отказа обеспечения заказа."""

    event_type = "OrderFulfillmentRejected"


class PaymentAuthorized(DomainEvent):
    """Факт успешной авторизации платежа в Payment."""

    event_type = "PaymentAuthorized"


class PaymentAuthorizationRejected(DomainEvent):
    """Факт отказа в авторизации платежа в контексте Payment."""

    event_type = "PaymentAuthorizationRejected"


class PaymentCaptured(DomainEvent):
    """Факт захвата ранее авторизованной суммы."""

    event_type = "PaymentCaptured"


class PaymentCaptureRejected(DomainEvent):
    """Факт невозможности захватить ранее авторизованную сумму."""

    event_type = "PaymentCaptureRejected"


class PaymentVoided(DomainEvent):
    """Факт аннулирования авторизованного платежа."""

    event_type = "PaymentVoided"


class InventoryReserved(DomainEvent):
    """Факт закрепления складского остатка за строкой заказа."""

    event_type = "InventoryReserved"


class InventoryReservationRejected(DomainEvent):
    """Факт нехватки остатка для запрошенного резерва."""

    event_type = "InventoryReservationRejected"


class InventoryReservationReleased(DomainEvent):
    """Факт освобождения ранее активного резерва."""

    event_type = "InventoryReservationReleased"


class ShipmentCreated(DomainEvent):
    """Факт создания отправления в Shipping."""

    event_type = "ShipmentCreated"


class ShipmentDispatched(DomainEvent):
    """Факт передачи созданного отправления перевозчику."""

    event_type = "ShipmentDispatched"


class ShipmentCancelled(DomainEvent):
    """Факт отмены ещё не переданного отправления."""

    event_type = "ShipmentCancelled"


class RaisesDomainEvents:
    """Примесь для корней, временно накапливающих новые факты команды."""

    def _start_event_recording(self) -> None:
        """Подготавливает пустой буфер фактов при создании или восстановлении."""
        self._events: list[DomainEvent] = []

    def _record(self, event: DomainEvent) -> None:
        """Добавляет факт в буфер до успешного commit Unit of Work."""
        self._events.append(event)

    def pending_events(self) -> tuple[DomainEvent, ...]:
        """Возвращает факты, которые необходимо сохранить с агрегатом."""
        return tuple(self._events)

    def clear_pending_events(self) -> None:
        """Очищает буфер только после успешной фиксации транзакции."""
        self._events.clear()


class Notification:
    """Плоское уведомление, производное от сохранённого доменного факта."""

    def __init__(self, position: int, event_id: str, context_name: str, event_type: str, aggregate_id: str, correlation_id: str, causation_id: str | None, payload: dict) -> None:
        """Сохраняет техническую оболочку и скалярный контракт события."""
        self.position = position
        self.event_id = event_id
        self.context_name = context_name
        self.event_type = event_type
        self.aggregate_id = aggregate_id
        self.correlation_id = correlation_id
        self.causation_id = causation_id
        self.payload = dict(payload)


class NotificationLog:
    """Читает единый журнал в глобальном порядке без записи предметных фактов."""

    def __init__(self, notification_reader) -> None:
        """Сохраняет фабрику Unit of Work текущего приложения."""
        self._notification_reader = notification_reader

    def next_notifications(self, subscriber_name: str, event_types: set[str], limit: int = 100) -> list[Notification]:
        """Возвращает необработанные уведомления, интересные одному подписчику.

        Args:
            subscriber_name: Устойчивое имя потребителя.
            event_types: Допустимые для него имена фактов.
            limit: Верхняя граница polling-порции.
        """
        return self._notification_reader.notifications_for(subscriber_name, event_types, limit)


class NotificationDispatcher:
    """Инфраструктурный polling-диспетчер одного явного подписчика."""

    def __init__(self, log: NotificationLog, subscriber_name: str, event_types: set[str], handler: Callable[[Notification], None]) -> None:
        """Настраивает контракт подписки и обработчик прикладного слоя."""
        self._log = log
        self._subscriber_name = subscriber_name
        self._event_types = set(event_types)
        self._handler = handler

    def dispatch(self, limit: int = 100) -> int:
        """Передаёт ограниченную порцию; receipt фиксирует сам обработчик.

        Обработчик обязан сохранить receipt, локальную команду и новые факты в
        одной транзакции. Исключение оставляет уведомление доступным для повтора.
        """
        notifications = self._log.next_notifications(self._subscriber_name, self._event_types, limit)
        for notification in notifications:
            self._handler(notification)
        return len(notifications)
