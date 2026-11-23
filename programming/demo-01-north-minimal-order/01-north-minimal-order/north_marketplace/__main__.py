"""CLI-входной адаптер минимального приложения."""

from __future__ import annotations

import json

from .application import OrderingApplicationService


def main() -> None:
    """Создаёт, наполняет и размещает один заказ."""
    service = OrderingApplicationService()
    service.create_order("order-001", "buyer-001", "RUB")
    service.add_paid_line("order-001", "line-001", "BOOK-DDD", "Реализация DDD", "1890.00", 1)
    service.set_shipping_address("order-001", "Архангельск, Поморская, 18")
    print(json.dumps(service.place("order-001"), ensure_ascii=False))


if __name__ == "__main__":
    main()
