"""Асинхронный REST-входной адаптер событийной стадии."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService, PaymentApplicationService, ShippingApplicationService
from .fulfillment_application import FulfillmentApplicationService
from .infrastructure import Database
from .kernel import ConcurrencyConflict, DomainRuleViolation


class CatalogItemRequest(BaseModel):
    """Тело команды создания предложения."""

    item_id: str
    sku: str
    title: str
    seller_id: str
    amount: str
    currency: str = "RUB"


class OrderRequest(BaseModel):
    """Тело команды создания черновика заказа."""

    order_id: str
    buyer_id: str
    currency: str = "RUB"


class LineRequest(BaseModel):
    """Тело команды добавления строки из каталога."""

    line_id: str
    catalog_item_id: str
    quantity: int


class PlacementRequest(BaseModel):
    """Тело асинхронной команды размещения."""

    address: str


class StockRequest(BaseModel):
    """Тело команды создания складской позиции."""

    item_id: str
    warehouse_id: str
    sku: str
    quantity: int


class ReservationRequest(BaseModel):
    """Тело совместимой ручной команды резерва."""

    reservation_id: str
    order_id: str
    sku: str
    quantity: int


class ShipmentRequest(BaseModel):
    """Тело совместимой ручной команды создания отправления."""

    shipment_id: str
    order_id: str


def create_app(database_url: str = "sqlite+pysqlite:///north-stage-03.db") -> FastAPI:
    """Создаёт REST-границу без предметных правил в контроллерах."""
    database = Database(database_url)
    catalog = CatalogApplicationService(database)
    inventory = InventoryApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    payment = PaymentApplicationService(database)
    shipping = ShippingApplicationService(database, ordering)
    fulfillment = FulfillmentApplicationService(database, database)
    app = FastAPI(title="Север: события и обеспечение")

    def invoke(action):
        """Переводит предметные ошибки в HTTP 422, а конфликт версии — в 409."""
        try:
            return action()
        except ConcurrencyConflict as error:
            raise HTTPException(409, str(error)) from error
        except (DomainRuleViolation, KeyError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/catalog/items")
    def create_catalog_item(request: CatalogItemRequest) -> dict:
        """Создаёт предложение Catalog."""
        return invoke(lambda: catalog.create_item(**request.model_dump()))

    @app.post("/inventory/items")
    def create_stock(request: StockRequest) -> dict:
        """Создаёт позицию Inventory."""
        return invoke(lambda: inventory.create_stock_item(**request.model_dump()))

    @app.post("/inventory/reservations")
    def reserve(request: ReservationRequest) -> dict:
        """Сохраняет совместимую ручную команду резерва через факт Inventory."""
        return invoke(lambda: inventory.reserve_by_sku(**request.model_dump(), causation_id="http-manual-reservation"))

    @app.post("/orders")
    def create_order(request: OrderRequest) -> dict:
        """Создаёт черновик Ordering."""
        return invoke(lambda: ordering.create_order(**request.model_dump()))

    @app.post("/orders/{order_id}/lines")
    def add_line(order_id: str, request: LineRequest) -> dict:
        """Добавляет локальный снимок каталожного предложения."""
        return invoke(lambda: ordering.add_catalog_line(order_id, **request.model_dump()))

    @app.post("/orders/{order_id}/place", status_code=202)
    def place(order_id: str, request: PlacementRequest) -> dict:
        """Фиксирует заказ и ``OrderPlaced`` без синхронного процесса."""
        return invoke(lambda: ordering.place(order_id, request.address))

    @app.post("/payments/{payment_id}/authorize")
    def authorize(payment_id: str, order_id: str, amount: str, currency: str = "RUB") -> dict:
        """Сохраняет ручную авторизацию с фактом владельца Payment."""
        return invoke(lambda: payment.authorize(payment_id, order_id, amount, currency, "http-manual-authorization"))

    @app.post("/payments/{payment_id}/capture")
    def capture(payment_id: str) -> dict:
        """Сохраняет ручный захват ранее авторизованного платежа."""
        return invoke(lambda: payment.capture(payment_id, causation_id="http-manual-capture"))

    @app.post("/shipments")
    def create_shipment(request: ShipmentRequest) -> dict:
        """Создаёт отправление из скалярного контракта размещённого заказа."""
        return invoke(lambda: shipping.create_for_order(**request.model_dump(), causation_id="http-manual-shipment"))

    @app.post("/shipments/{shipment_id}/dispatch")
    def dispatch_shipment(shipment_id: str) -> list[str]:
        """Передаёт один Shipment отдельной локальной транзакцией."""
        return invoke(lambda: shipping.dispatch_ready([shipment_id], "http-manual-dispatch"))

    @app.post("/processing/dispatch")
    def dispatch(limit: int = 100) -> dict:
        """Принимает polling-уведомления и сохраняет будущие work-item.

        Note:
            Исполнение команд намеренно вынесено в ``/processing/resume``.
            Поэтому HTTP-цикл показывает ту же границу остановки, что и CLI.
        """
        return invoke(lambda: {"delivered": fulfillment.dispatcher().dispatch(limit)})

    @app.post("/processing/resume")
    def resume(limit: int = 100) -> dict:
        """Исполняет сохранённые команды процесса отдельными транзакциями.

        Note:
            Маршрут намеренно не читает новые уведомления из журнала. Он
            сохраняет разделение ``dispatch → resume`` из CLI: факт сначала
            создаёт устойчивую команду процесса, а затем команда изменяет
            агрегат-владелец в собственной локальной транзакции.
        """
        return invoke(lambda: {"executed": fulfillment.dispatch_commands(limit)})

    @app.get("/orders/{order_id}")
    def get_order(order_id: str) -> dict:
        """Возвращает текущее состояние заказа."""
        return invoke(lambda: ordering.get(order_id))

    @app.get("/fulfillment/{order_id}")
    def get_process(order_id: str) -> dict:
        """Возвращает сохранённое состояние длительного процесса."""
        state = fulfillment.state(order_id)
        if state is None:
            raise HTTPException(404, "Процесс не найден.")
        return state

    return app
