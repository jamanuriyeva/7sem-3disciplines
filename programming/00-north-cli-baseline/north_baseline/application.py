"""Функции сценария оформления заказа без предметной модели."""

from __future__ import annotations

from decimal import Decimal


def initial_state() -> dict:
    """Создаёт изменяемый JSON-совместимый набор данных демонстрации.

    Returns:
        Словарь с каталогом, остатками, корзиной и пустым списком заказов.
    """
    return {
        "catalog": {
            "BOOK-DDD": {"name": "Реализация DDD", "price": "1890.00", "currency": "RUB"},
            "MARK-NORTH": {"name": "Закладка Север", "price": "0.00", "currency": "RUB"},
        },
        "stock": {"BOOK-DDD": 5, "MARK-NORTH": 10},
        "cart": [],
        "orders": [],
    }


def add_to_cart(state: dict, sku: str, quantity: int) -> None:
    """Добавляет строку в корзину прямым изменением общего словаря.

    Args:
        state: Общее изменяемое состояние приложения.
        sku: Код существующего товара.
        quantity: Положительное количество.

    Raises:
        ValueError: Товар не найден либо количество некорректно.
    """
    if sku not in state["catalog"]:
        raise ValueError("Товар отсутствует в каталоге.")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("Количество должно быть положительным целым числом.")
    state["cart"].append({"sku": sku, "quantity": quantity})


def checkout(state: dict, address: str) -> dict:
    """Оформляет корзину и немедленно уменьшает остатки.

    Args:
        state: Общее изменяемое состояние приложения.
        address: Строка адреса доставки.

    Returns:
        Новый словарь заказа, уже добавленный в список заказов.

    Raises:
        ValueError: Корзина пуста, адрес пуст либо остатка недостаточно.

    Note:
        Функция намеренно показывает хрупкую процедуру: правило валюты,
        цена-снимок и граница изменения здесь отсутствуют.
    """
    if not state["cart"]:
        raise ValueError("Нельзя оформить пустую корзину.")
    if not isinstance(address, str) or not address.strip():
        raise ValueError("Адрес доставки обязателен.")
    total = Decimal("0")
    lines = []
    for cart_line in state["cart"]:
        sku = cart_line["sku"]
        quantity = cart_line["quantity"]
        if state["stock"][sku] < quantity:
            raise ValueError(f"Недостаточно товара {sku}.")
        product = state["catalog"][sku]
        state["stock"][sku] -= quantity
        price = Decimal(product["price"])
        total += price * quantity
        lines.append({"sku": sku, "quantity": quantity, "unit_price": product["price"]})
    order = {
        "id": f"order-{len(state['orders']) + 1:03d}",
        "address": address.strip(),
        "lines": lines,
        "total": f"{total:.2f}",
        "status": "PLACED",
    }
    state["orders"].append(order)
    state["cart"].clear()
    return order
