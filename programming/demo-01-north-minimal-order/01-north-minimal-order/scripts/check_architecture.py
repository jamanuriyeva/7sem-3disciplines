"""Проверяет границу минимального агрегата Ordering."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    """Не допускает HTTP и инфраструктуру внутрь доменного модуля."""
    root = Path(__file__).resolve().parents[1] / "north_marketplace"
    domain = (root / "ordering.py").read_text(encoding="utf-8")
    forbidden = ("FastAPI", "sqlalchemy", "Request", "Response")
    found = [term for term in forbidden if term in domain]
    if found:
        raise SystemExit(f"Домен минимального заказа импортирует инфраструктуру: {', '.join(found)}")
    application = (root / "application.py").read_text(encoding="utf-8")
    if "OrderingApplicationService" not in application:
        raise SystemExit("Отсутствует прикладная служба OrderingApplicationService.")
    print("Граница агрегата и прикладной службы соблюдена.")


if __name__ == "__main__":
    main()
