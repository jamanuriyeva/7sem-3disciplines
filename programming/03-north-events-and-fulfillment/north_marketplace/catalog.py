"""Контекст Catalog: предложения и расчёт ценовых снимков."""

from __future__ import annotations

from .kernel import DomainRuleViolation, Identifier, Money, SellerId, Sku


class CatalogItemId(Identifier):
    """Идентифицирует корень предложения в каталоге."""


class PriceQuote:
    """Представляет ценовой снимок, передаваемый в контекст Ordering."""

    def __init__(self, sku: Sku, title: str, seller_id: SellerId, price: Money) -> None:
        """Создаёт неизменяемый результат доменной службы."""
        self._sku, self._title, self._seller_id, self._price = sku, title, seller_id, price

    def to_dict(self) -> dict:
        """Возвращает скалярное представление результата."""
        return {"sku": self._sku.value(), "title": self._title, "seller_id": self._seller_id.value(), "amount": str(self._price.amount()), "currency": self._price.currency()}


class CatalogItem:
    """Корень агрегата предложения, владеющий текущей ценой и доступностью."""

    def __init__(self, item_id: CatalogItemId, sku: Sku, title: str, seller_id: SellerId, price: Money, active: bool = True) -> None:
        """Создаёт предложение через фабрику ``create``."""
        if not title.strip() or price.amount() <= 0:
            raise DomainRuleViolation("Предложение требует названия и положительной цены.")
        self._id, self._sku, self._title, self._seller_id, self._price, self._active = item_id, sku, title.strip(), seller_id, price, active

    @classmethod
    def create(cls, item_id: CatalogItemId, sku: Sku, title: str, seller_id: SellerId, price: Money) -> "CatalogItem":
        """Создаёт активное предложение.

        Returns:
            Новый корень ``CatalogItem``.
        """
        return cls(item_id, sku, title, seller_id, price)

    def revise_price(self, price: Money) -> None:
        """Меняет цену активного предложения.

        Raises:
            DomainRuleViolation: Цена неположительна или предложение снято.
        """
        if not self._active or price.amount() <= 0:
            raise DomainRuleViolation("Нельзя назначить цену снятому или бесплатному предложению.")
        self._price = price

    def quote(self) -> PriceQuote:
        """Возвращает ценовой снимок без передачи корня другому контексту.

        Raises:
            DomainRuleViolation: Предложение неактивно.
        """
        if not self._active:
            raise DomainRuleViolation("Снятое предложение нельзя включить в заказ.")
        return PriceQuote(self._sku, self._title, self._seller_id, self._price)

    def id(self) -> CatalogItemId:
        """Возвращает идентификатор корня."""
        return self._id

    def to_state(self) -> dict:
        """Подготавливает простое состояние для Data Mapper."""
        return {"id": self._id.value(), "sku": self._sku.value(), "title": self._title, "seller_id": self._seller_id.value(), "amount": str(self._price.amount()), "currency": self._price.currency(), "active": self._active}

    @classmethod
    def from_state(cls, state: dict) -> "CatalogItem":
        """Восстанавливает предметный корень из состояния хранилища."""
        return cls(CatalogItemId(state["id"]), Sku(state["sku"]), state["title"], SellerId(state["seller_id"]), Money(state["amount"], state["currency"]), bool(state["active"]))


class CatalogPricingService:
    """Собирает ценовые снимки из нескольких предложений без их изменения."""

    def quote(self, items: list[CatalogItem]) -> list[PriceQuote]:
        """Возвращает актуальные снимки нескольких предложений.

        Args:
            items: Корни каталога, отобранные прикладным слоем.

        Returns:
            Независимые ценовые снимки.
        """
        return [item.quote() for item in items]
