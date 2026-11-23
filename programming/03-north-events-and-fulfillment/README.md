# Север 03. События и процесс обеспечения

Стадия показывает длительный процесс между контекстами Ordering, Payment,
Inventory и Shipping. Размещение заказа меняет корень `Order` и в той же
локальной транзакции добавляет `OrderPlaced` в единый append-only журнал.
Текущие состояния агрегатов по-прежнему восстанавливаются репозиториями из
ORM-строк: журнал не является источником восстановления и эта модель не
является Event Sourcing.

`FulfillmentProcess` хранит этап, идентификаторы и устойчивые локальные
work-item'ы. Он не создаёт факты за чужие контексты: `Payment`, `StockItem`,
`Shipment` и `Order` сами регистрируют факты после успешных предметных
операций. Receipt уведомления, новое состояние процесса и следующий work-item
фиксируются в одной локальной транзакции; целевой агрегат выполняет свою
команду отдельной локальной транзакцией.

Прикладные службы зависят от портов локальной транзакции и чтения уведомлений.
SQLite и SQLAlchemy реализуют эти порты в инфраструктуре; агрегаты не знают ни
о журнале, ни о polling-доставке.

```bash
uv run python -m north_marketplace prepare --scenario success --database demo.db --reset
uv run python -m north_marketplace dispatch --database demo.db
uv run python -m north_marketplace resume --database demo.db
uv run python -m north_marketplace state --database demo.db
uv run pytest
uv run uvicorn north_marketplace.api:create_app --factory
```

HTTP-команда размещения возвращает `202`: она не выполняет процесс скрыто в
одном запросе. `POST /processing/dispatch` принимает сохранённые уведомления,
а `POST /processing/resume` исполняет ограниченную порцию устойчивых команд
процесса. Ручные ресурсы склада, платежа и отправления остаются доступны как
совместимый путь предыдущей стадии; их агрегаты также сохраняют собственные
факты в журнале.
