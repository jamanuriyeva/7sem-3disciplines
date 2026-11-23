"""Прикладная служба и in-memory хранилище первого контекста."""

from __future__ import annotations

from .ordering import BuyerId, Money, Order, OrderId, OrderLine, OrderLineId, Quantity, Sku


class OrderingApplicationService:
    """Переводит команды интерфейса в вызовы одного корня ``Order``."""

    def __init__(self) -> None:
        """Создаёт учебное временное хранилище заказов в памяти."""
        self._orders: dict[str, Order] = {}

    def create_order(self, order_id: str, buyer_id: str, currency: str) -> dict:
        """Создаёт и сохраняет черновик заказа.

        Args:
            order_id: Идентификатор нового заказа.
            buyer_id: Внешний идентификатор покупателя.
            currency: Валюта заказа.

        Returns:
            Снимок созданного заказа.
        """
        order = Order.create_draft(OrderId(order_id), BuyerId(buyer_id), currency)
        self._orders[order_id] = order
        return order.snapshot()

    def add_paid_line(self, order_id: str, line_id: str, sku: str, title: str, amount: str, quantity: int) -> dict:
        """Создаёт строку фабрикой и добавляет её в один заказ."""
        order = self._require(order_id)
        line = OrderLine.create_paid(OrderLineId(line_id), Sku(sku), title, Money(amount, order.snapshot()["currency"]), Quantity(quantity))
        order.add_paid_line(line)
        return order.snapshot()

    def set_shipping_address(self, order_id: str, address: str) -> dict:
        """Передаёт адрес корню и возвращает его снимок."""
        order = self._require(order_id)
        order.set_shipping_address(address)
        return order.snapshot()

    def place(self, order_id: str) -> dict:
        """Размещает один подготовленный заказ."""
        order = self._require(order_id)
        order.place()
        return order.snapshot()

    def get(self, order_id: str) -> dict:
        """Возвращает снимок заказа по его идентификатору."""
        return self._require(order_id).snapshot()

    def _require(self, order_id: str) -> Order:
        """Находит обязательный корень либо сообщает об отсутствии.

        Raises:
            KeyError: Заказ ещё не создан.
        """
        if order_id not in self._orders:
            raise KeyError("Заказ не найден.")
        return self._orders[order_id]
