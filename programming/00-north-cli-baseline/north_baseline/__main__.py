"""Командная точка входа процедурной демонстрации."""

from __future__ import annotations

import json

from .application import add_to_cart, checkout, initial_state


def main() -> None:
    """Выполняет один воспроизводимый сценарий оформления заказа."""
    state = initial_state()
    add_to_cart(state, "BOOK-DDD", 1)
    add_to_cart(state, "MARK-NORTH", 1)
    print(json.dumps(checkout(state, "Архангельск, Поморская, 18"), ensure_ascii=False))


if __name__ == "__main__":
    main()
