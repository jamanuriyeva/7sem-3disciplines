"""Скалярные контракты синхронного взаимодействия ограниченных контекстов.

Контракт не является доменным объектом ни Catalog, ни Ordering, ни Shipping.
Он переносит только опубликованные значения на прикладной границе и не даёт
контексту-получателю получить объект чужого агрегата.
"""

from __future__ import annotations


class PublishedOfferContract:
    """Опубликованное предложение Catalog для создания локального снимка."""

    def __init__(self, sku: str, title: str, seller_id: str, amount: str, currency: str) -> None:
        """Сохраняет только скалярные значения публичного предложения."""
        self.sku = sku
        self.title = title
        self.seller_id = seller_id
        self.amount = amount
        self.currency = currency


class OrderShipmentContract:
    """Снимок размещённого заказа, достаточный контексту Shipping."""

    def __init__(self, order_id: str, address: str, lines: list[dict]) -> None:
        """Копирует идентификатор, адрес и физический состав отправления."""
        self.order_id = order_id
        self.address = address
        self.lines = [dict(line) for line in lines]
