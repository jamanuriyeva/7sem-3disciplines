"""REST-входной адаптер контекста Ordering с веб-интерфейсом."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .application import OrderingApplicationService
from .ordering import DomainRuleViolation


class CreateOrderRequest(BaseModel):
    order_id: str
    buyer_id: str
    currency: str = "RUB"


class AddLineRequest(BaseModel):
    line_id: str
    sku: str
    title: str
    amount: str
    quantity: int


class AddressRequest(BaseModel):
    address: str


# --- HTML ШАБЛОН (Наш веб-интерфейс) ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Билеты на мероприятие</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f0f4f8; padding: 20px; }
        .container { max-width: 800px; margin: auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; }
        .step { margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }
        .step h3 { margin-top: 0; color: #3498db; }
        input { padding: 8px; margin: 5px 0; width: 100%; box-sizing: border-box; }
        button { background: #3498db; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; }
        button:hover { background: #2980b9; }
        #result { margin-top: 20px; padding: 15px; background: #e8f8f5; border: 1px solid #1abc9c; white-space: pre-wrap; font-family: monospace; display: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎟️ Система бронирования билетов</h1>
        
        <div class="step">
            <h3>1. Создать бронь</h3>
            <input type="text" id="order_id" placeholder="Номер брони (например, booking-1)" value="booking-1">
            <input type="text" id="buyer_id" placeholder="ID покупателя (например, user-1)" value="user-1">
            <button onclick="createOrder()">Создать бронь</button>
        </div>

        <div class="step">
            <h3>2. Добавить билет</h3>
            <input type="text" id="line_id" placeholder="Номер билета (например, ticket-1)" value="ticket-1">
            <input type="text" id="sku" placeholder="Код сеанса (например, MOVIE-1)" value="MOVIE-1">
            <input type="text" id="title" placeholder="Название (например, Бэтмен)" value="Бэтмен">
            <input type="number" id="amount" placeholder="Цена (например, 500)" value="500">
            <input type="number" id="quantity" placeholder="Количество мест" value="1">
            <button onclick="addLine()">Добавить билет</button>
        </div>

        <div class="step">
            <h3>3. Указать место и оплатить</h3>
            <input type="text" id="address" placeholder="Ряд и место (например, Ряд 5, Место 12)" value="Ряд 5, Место 12">
            <button onclick="setAddress()">Зафиксировать место</button>
            <br><br>
            <button onclick="placeOrder()" style="background: #e67e22;">Оплатить и подтвердить</button>
        </div>

        <div id="result"></div>
    </div>

    <script>
        const baseUrl = '/orders';
        const resultDiv = document.getElementById('result');

        function showResult(data) {
            resultDiv.style.display = 'block';
            resultDiv.innerText = JSON.stringify(data, null, 2);
        }

        async function sendRequest(url, method, body) {
            const response = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: body ? JSON.stringify(body) : undefined
            });
            const data = await response.json();
            showResult(data);
            if (!response.ok) alert('Ошибка: ' + (data.detail || 'Неизвестная ошибка'));
        }

        function createOrder() {
            sendRequest(baseUrl, 'POST', {
                order_id: document.getElementById('order_id').value,
                buyer_id: document.getElementById('buyer_id').value,
                currency: 'RUB'
            });
        }

        function addLine() {
            const orderId = document.getElementById('order_id').value;
            sendRequest(`${baseUrl}/${orderId}/lines`, 'POST', {
                line_id: document.getElementById('line_id').value,
                sku: document.getElementById('sku').value,
                title: document.getElementById('title').value,
                amount: document.getElementById('amount').value,
                quantity: parseInt(document.getElementById('quantity').value)
            });
        }

        function setAddress() {
            const orderId = document.getElementById('order_id').value;
            sendRequest(`${baseUrl}/${orderId}/address`, 'POST', {
                address: document.getElementById('address').value
            });
        }

        function placeOrder() {
            const orderId = document.getElementById('order_id').value;
            sendRequest(`${baseUrl}/${orderId}/place`, 'POST');
        }
    </script>
</body>
</html>
"""


def create_app(service: OrderingApplicationService | None = None) -> FastAPI:
    application_service = service or OrderingApplicationService()
    app = FastAPI(title="Билеты: Ordering")

    def invoke(action):
        try:
            return action()
        except (DomainRuleViolation, KeyError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    # --- НОВЫЙ МАРШРУТ ДЛЯ САЙТА ---
    @app.get("/", response_class=HTMLResponse)
    def index():
        """Отдаёт главную страницу с интерфейсом."""
        return HTML_PAGE

    @app.post("/orders")
    def create_order(request: CreateOrderRequest) -> dict:
        return invoke(lambda: application_service.create_order(request.order_id, request.buyer_id, request.currency))

    @app.post("/orders/{order_id}/lines")
    def add_line(order_id: str, request: AddLineRequest) -> dict:
        return invoke(lambda: application_service.add_paid_line(order_id, request.line_id, request.sku, request.title, request.amount, request.quantity))

    @app.post("/orders/{order_id}/address")
    def set_address(order_id: str, request: AddressRequest) -> dict:
        return invoke(lambda: application_service.set_shipping_address(order_id, request.address))

    @app.post("/orders/{order_id}/place")
    def place(order_id: str) -> dict:
        return invoke(lambda: application_service.place(order_id))

    @app.get("/orders/{order_id}")
    def get(order_id: str) -> dict:
        return invoke(lambda: application_service.get(order_id))

    return app