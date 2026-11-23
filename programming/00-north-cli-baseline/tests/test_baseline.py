"""Проверки процедурного исходного сценария."""

import pytest

from north_baseline.application import add_to_cart, checkout, initial_state


def test_checkout_reduces_stock_and_keeps_price_text() -> None:
    """Оформление уменьшает остаток и сохраняет значение цены в заказе."""
    state = initial_state()
    add_to_cart(state, "BOOK-DDD", 1)
    order = checkout(state, "Архангельск")
    assert order["total"] == "1890.00"
    assert state["stock"]["BOOK-DDD"] == 4


def test_checkout_rejects_insufficient_stock() -> None:
    """Оформление не позволяет вычесть отрицательный остаток."""
    state = initial_state()
    add_to_cart(state, "BOOK-DDD", 6)
    with pytest.raises(ValueError, match="Недостаточно"):
        checkout(state, "Архангельск")
