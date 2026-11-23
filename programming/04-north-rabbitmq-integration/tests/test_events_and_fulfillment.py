"""Интеграционные проверки фактического процесса обеспечения заказа."""

from sqlalchemy import select

from north_marketplace.application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService, PaymentApplicationService, ShippingApplicationService
from north_marketplace.fulfillment_application import FulfillmentApplicationService
from north_marketplace.fulfillment import FulfillmentProcess
from north_marketplace.infrastructure import Database, DomainEventRow


def _prepared_database(mark_quantity: int, ordered_marks: int) -> tuple[Database, OrderingApplicationService, FulfillmentApplicationService]:
    """Создаёт заказ из двух строк и остатки для успешного либо отказного пути."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    inventory = InventoryApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    catalog.create_item("book", "BOOK", "Книга", "seller", "10.00", "RUB")
    catalog.create_item("mark", "MARK", "Закладка", "seller", "1.00", "RUB")
    inventory.create_stock_item("stock-book", "warehouse", "BOOK", 2)
    inventory.create_stock_item("stock-mark", "warehouse", "MARK", mark_quantity)
    ordering.create_order("order-1", "buyer", "RUB")
    ordering.add_catalog_line("order-1", "line-book", "book", 1)
    ordering.add_catalog_line("order-1", "line-mark", "mark", ordered_marks)
    ordering.place("order-1", "Архангельск, 1")
    return database, ordering, FulfillmentApplicationService(database, database)


def _drain(service: FulfillmentApplicationService) -> None:
    """Доставляет все доступные уведомления по одному для проверки restart-safe пути."""
    while service.resume(1):
        pass


def test_successful_process_changes_real_aggregates() -> None:
    """Успех создаёт платёж, резервы, отправление и итоговое состояние заказа."""
    database, ordering, service = _prepared_database(2, 1)
    _drain(service)
    assert ordering.get("order-1")["status"] == "FULFILLED"
    assert service.state("order-1")["stage"] == "COMPLETED"
    with database.unit_of_work() as uow:
        rows = uow.session.execute(select(DomainEventRow.event_type)).scalars().all()
    assert {"OrderPlaced", "PaymentAuthorized", "InventoryReserved", "ShipmentCreated", "PaymentCaptured", "OrderFulfilled"}.issubset(set(rows))


def test_inventory_failure_releases_real_reservation_and_voids_payment() -> None:
    """Нехватка второй строки освобождает первую и завершает заказ отказом."""
    database, ordering, service = _prepared_database(1, 2)
    _drain(service)
    assert ordering.get("order-1")["status"] == "FULFILLMENT_REJECTED"
    assert service.state("order-1")["stage"] == "REJECTED"
    with database.unit_of_work() as uow:
        rows = uow.session.execute(select(DomainEventRow.event_type)).scalars().all()
    assert {"InventoryReservationRejected", "InventoryReservationReleased", "PaymentVoided", "OrderFulfillmentRejected"}.issubset(set(rows))


def test_restart_resumes_pending_command_without_duplicate_fact() -> None:
    """Новая служба продолжает процесс после остановки между polling-порциями."""
    database, ordering, first = _prepared_database(2, 1)
    assert first.resume(1) == 1
    resumed = FulfillmentApplicationService(database, database)
    _drain(resumed)
    assert ordering.get("order-1")["status"] == "FULFILLED"
    with database.unit_of_work() as uow:
        events = uow.session.execute(select(DomainEventRow.event_type)).scalars().all()
    assert events.count("PaymentAuthorized") == 1


def test_resume_runs_command_left_after_receipt_commit() -> None:
    """Новый запуск исполняет команду, сохранённую до аварии после receipt."""
    database, ordering, _ = _prepared_database(2, 1)
    notification = database.notifications_for("probe", {"OrderPlaced"}, 1)[0]
    with database.unit_of_work() as uow:
        process = FulfillmentProcess.start(notification.payload)
        uow.processes().save(process)
        uow.process_commands().enqueue("order-1:order-placed:AUTHORIZE_PAYMENT:0", "order-1", process.first_command())
        uow.mark_handled("FulfillmentApplicationService", notification.position)
    resumed = FulfillmentApplicationService(database, database)
    assert resumed.resume(1) == 1
    _drain(resumed)
    assert ordering.get("order-1")["status"] == "FULFILLED"


def test_resume_repeats_idempotent_command_after_target_commit() -> None:
    """Повтор после commit целевого агрегата не создаёт второй платёжный факт."""
    database, ordering, first = _prepared_database(2, 1)
    notification = database.notifications_for("probe", {"OrderPlaced"}, 1)[0]
    with database.unit_of_work() as uow:
        process = FulfillmentProcess.start(notification.payload)
        uow.processes().save(process)
        uow.process_commands().enqueue("order-1:order-placed:AUTHORIZE_PAYMENT:0", "order-1", process.first_command())
        uow.mark_handled(first.subscriber_name, notification.position)
    first._execute("order-1", {"name": "AUTHORIZE_PAYMENT"}, notification.event_id)
    resumed = FulfillmentApplicationService(database, database)
    assert resumed.resume(1) == 1
    _drain(resumed)
    assert ordering.get("order-1")["status"] == "FULFILLED"
    with database.unit_of_work() as uow:
        events = uow.session.execute(select(DomainEventRow.event_type)).scalars().all()
    assert events.count("PaymentAuthorized") == 1


def test_capture_rejection_cancels_shipment_before_stock_and_payment_compensation() -> None:
    """Отказ захвата после отправления запускает полную обратную цепочку."""
    database, ordering, service = _prepared_database(2, 1)
    while service.state("order-1") is None or service.state("order-1")["stage"] != "WAITING_CAPTURE":
        service.resume(1)
    PaymentApplicationService(database).reject_capture("payment-order-1", "order-1", "Шлюз отклонил захват", "manual-capture-rejection")
    _drain(service)
    assert ordering.get("order-1")["status"] == "FULFILLMENT_REJECTED"
    with database.unit_of_work() as uow:
        events = set(uow.session.execute(select(DomainEventRow.event_type)).scalars().all())
    assert {"PaymentCaptureRejected", "ShipmentCancelled", "InventoryReservationReleased", "PaymentVoided", "OrderFulfillmentRejected"}.issubset(events)


def test_inherited_manual_shipping_command_records_fact_of_shipment_owner() -> None:
    """Ручная передача Shipment сохраняет собственный факт без запуска процесса."""
    database = Database("sqlite+pysqlite:///:memory:")
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    shipping = ShippingApplicationService(database, ordering)
    catalog.create_item("book", "BOOK", "Книга", "seller", "10.00", "RUB")
    ordering.create_order("manual-order", "buyer", "RUB")
    ordering.add_catalog_line("manual-order", "manual-line", "book", 1)
    ordering.place("manual-order", "Архангельск, 1")
    shipping.create_for_order("manual-shipment", "manual-order")
    assert shipping.dispatch_ready(["manual-shipment"]) == ["manual-shipment"]
    with database.unit_of_work() as uow:
        events = uow.session.execute(select(DomainEventRow.event_type)).scalars().all()
    assert "ShipmentDispatched" in events
