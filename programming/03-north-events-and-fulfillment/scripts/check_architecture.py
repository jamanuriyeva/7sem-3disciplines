"""Проверяет учебные границы доменной модели и соглашения об именах."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_MODULES = {"kernel.py", "catalog.py", "ordering.py", "inventory.py", "payment.py", "shipping.py"}
APPLICATION_MODULES = {"application.py", "fulfillment_application.py", "rabbit_fulfillment.py"}
FORBIDDEN = {"fastapi", "sqlalchemy", "pika", "pydantic"}
CONTEXT_MODULES = {"catalog", "ordering", "inventory", "payment", "shipping"}


def imports_of(tree: ast.AST) -> set[str]:
    """Возвращает верхние части импортируемых имён.

    Args:
        tree: Разобранное синтаксическое дерево модуля.

    Returns:
        Набор имён внешних модулей.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def check() -> list[str]:
    """Собирает нарушения из доменных модулей и имён классов.

    Returns:
        Текстовые описания найденных нарушений.
    """
    violations: list[str] = []
    for source in (ROOT / "north_marketplace").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        if source.name in DOMAIN_MODULES:
            forbidden = imports_of(tree) & FORBIDDEN
            if forbidden:
                violations.append(f"{source.name}: домен импортирует {sorted(forbidden)}")
            imported_contexts = (imports_of(tree) & CONTEXT_MODULES) - {source.stem}
            if imported_contexts:
                violations.append(f"{source.name}: домен импортирует чужой контекст {sorted(imported_contexts)}")
        if source.name in APPLICATION_MODULES:
            if "sqlalchemy" in imports_of(tree):
                violations.append(f"{source.name}: прикладной слой импортирует SQLAlchemy вместо Repository.")
            if "infrastructure" in imports_of(tree):
                violations.append(f"{source.name}: прикладной слой зависит от конкретной инфраструктуры вместо порта.")
            if any(isinstance(node, ast.Name) and node.id.endswith("Row") for node in ast.walk(tree)):
                violations.append(f"{source.name}: прикладной слой обращается к ORM-строке.")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if "ApplicationService" in node.name and not node.name.endswith("ApplicationService"):
                violations.append(f"{source.name}: некорректное имя прикладной службы {node.name}")
            if node.name.endswith("Service") and not node.name.endswith("ApplicationService") and node.name not in {"CatalogPricingService", "WarehouseAllocationService", "ShipmentPlanningService"}:
                violations.append(f"{source.name}: неявная доменная служба {node.name}")
    return violations


def main() -> None:
    """Завершает проверку ненулевым кодом при нарушении архитектурных правил."""
    violations = check()
    if violations:
        raise SystemExit("\n".join(violations))
    print("Архитектурные границы и соглашения об именах соблюдены.")


if __name__ == "__main__":
    main()
