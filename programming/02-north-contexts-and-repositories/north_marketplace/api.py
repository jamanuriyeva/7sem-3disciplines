"""REST-входные адаптеры пяти контекстов без предметных правил."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .application import CatalogApplicationService, InventoryApplicationService, OrderingApplicationService, PaymentApplicationService, ShippingApplicationService
from .infrastructure import Database
from .kernel import ConcurrencyConflict, DomainRuleViolation


class CatalogItemRequest(BaseModel):
    """Тело HTTP-команды создания каталожного предложения."""
    item_id: str
    sku: str
    title: str
    seller_id: str
    amount: str
    currency: str = "RUB"


class OrderRequest(BaseModel):
    """Тело HTTP-команды создания черновика заказа."""
    order_id: str
    buyer_id: str
    currency: str = "RUB"


class CatalogLineRequest(BaseModel):
    """Тело HTTP-команды добавления снимка предложения в заказ."""
    line_id: str
    catalog_item_id: str
    quantity: int


class PlacementRequest(BaseModel):
    """Тело HTTP-команды размещения заказа."""
    address: str


class StockRequest(BaseModel):
    """Тело HTTP-команды создания складской позиции."""
    item_id: str
    warehouse_id: str
    sku: str
    quantity: int


class ReservationRequest(BaseModel):
    """Тело команды резерва после выбора позиции по товарному коду."""

    reservation_id: str
    order_id: str
    sku: str
    quantity: int


class ShipmentRequest(BaseModel):
    """Тело команды создания отправления из сохранённого заказа."""

    shipment_id: str
    order_id: str


def create_app(database_url: str = "sqlite+pysqlite:///north-stage-02.db") -> FastAPI:
    """Создаёт HTTP-границу с контекстными ресурсами.

    Args:
        database_url: URL изолированной SQLite-базы приложения.

    Returns:
        Настроенное приложение FastAPI.
    """
    database = Database(database_url)
    catalog = CatalogApplicationService(database)
    ordering = OrderingApplicationService(database, catalog)
    inventory, payment = InventoryApplicationService(database), PaymentApplicationService(database)
    shipping = ShippingApplicationService(database, ordering)
    app = FastAPI(title="Север: контексты и репозитории")

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
        """Создаёт ресурс предложения в контексте Catalog."""
        return invoke(lambda: catalog.create_item(**request.model_dump()))

    @app.post("/orders")
    def create_order(request: OrderRequest) -> dict:
        """Создаёт ресурс заказа в контексте Ordering."""
        return invoke(lambda: ordering.create_order(**request.model_dump()))

    @app.post("/orders/{order_id}/lines")
    def add_line(order_id: str, request: CatalogLineRequest) -> dict:
        """Добавляет ценовой снимок предложения в заказ."""
        return invoke(lambda: ordering.add_catalog_line(order_id, **request.model_dump()))

    @app.post("/orders/{order_id}/place")
    def place(order_id: str, request: PlacementRequest) -> dict:
        """Размещает подготовленный заказ."""
        return invoke(lambda: ordering.place(order_id, request.address))

    @app.post("/inventory/items")
    def create_stock(request: StockRequest) -> dict:
        """Создаёт ресурс складской позиции."""
        return invoke(lambda: inventory.create_stock_item(**request.model_dump()))

    @app.post("/inventory/reservations")
    def reserve(request: ReservationRequest) -> dict:
        """Выбирает складскую позицию по SKU и создаёт резерв."""
        return invoke(lambda: inventory.reserve_by_sku(**request.model_dump()))

    @app.post("/payments/{payment_id}/authorize")
    def authorize(payment_id: str, order_id: str, amount: str, currency: str = "RUB") -> dict:
        """Создаёт и авторизует учебный платёж."""
        return invoke(lambda: payment.authorize(payment_id, order_id, amount, currency))

    @app.post("/payments/{payment_id}/capture")
    def capture(payment_id: str) -> dict:
        """Захватывает ранее авторизованный платёж."""
        return invoke(lambda: payment.capture(payment_id))

    @app.post("/shipments")
    def create_shipment(request: ShipmentRequest) -> dict:
        """Создаёт отправление из снимка сохранённого заказа."""
        return invoke(lambda: shipping.create_for_order(**request.model_dump()))

    @app.post("/shipments/{shipment_id}/dispatch")
    def dispatch(shipment_id: str) -> list[str]:
        """Передаёт созданное отправление в доставку."""
        return invoke(lambda: shipping.dispatch_ready([shipment_id]))

    @app.get("/orders/{order_id}")
    def get_order(order_id: str) -> dict:
        """Возвращает текущее состояние заказа."""
        return invoke(lambda: ordering.get(order_id))

    return app
