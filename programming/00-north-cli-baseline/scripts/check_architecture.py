"""Проверяет, что процедурная исходная стадия не маскируется под DDD."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    """Отклоняет преждевременное появление HTTP, ORM и терминов агрегата."""
    source = (Path(__file__).resolve().parents[1] / "north_baseline" / "application.py").read_text(encoding="utf-8")
    forbidden = ("FastAPI", "sqlalchemy", "class Order", "Aggregate")
    found = [term for term in forbidden if term in source]
    if found:
        raise SystemExit(f"Исходная стадия не должна содержать: {', '.join(found)}")
    print("Процедурная исходная стадия не содержит преждевременных DDD-зависимостей.")


if __name__ == "__main__":
    main()
