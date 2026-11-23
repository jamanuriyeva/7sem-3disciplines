"""Проверки агрегата и HTTP-адаптера первого этапа."""

from fastapi.testclient import TestClient
import pytest

from north_marketplace.api import create_app
from north_marketplace.application import OrderingApplicationService
from north_marketplace.ordering import DomainRuleViolation


def test_order_keeps_price_snapshot_and_requires_address() -> None:
    """Корень фиксирует цену строки и не размещается без адреса."""
    service = OrderingApplicationService()
    service.create_order("o-1", "b-1", "RUB")
    service.add_paid_line("o-1", "l-1", "BOOK", "Книга", "10.00", 2)
    with pytest.raises(DomainRuleViolation, match="адрес"):
        service.place("o-1")
    service.set_shipping_address("o-1", "Архангельск")
    assert service.place("o-1")["total"] == "20.00"


def test_http_adapter_translates_command_to_application_service() -> None:
    """REST-адаптер вызывает прикладную службу и выдаёт снимок."""
    client = TestClient(create_app())
    response = client.post("/orders", json={"order_id": "o-2", "buyer_id": "b-2", "currency": "RUB"})
    assert response.status_code == 200
    assert response.json()["status"] == "DRAFT"
