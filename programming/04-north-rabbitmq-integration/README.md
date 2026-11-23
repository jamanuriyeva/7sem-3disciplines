# Север 04. RabbitMQ-интеграция

Эта стадия сохраняет предметную модель, единый append-only журнал и процесс
обеспечения третьей стадии. Изменяется транспорт уведомлений: стабильный
`NotificationMessage` попадает в persistent outbox одновременно с локальным
состоянием и фактом, затем `OutboxRelay` публикует его в RabbitMQ.

`Notification` остаётся неизменяемой записью со скалярными данными.
`NotificationMessage` добавляет только версию контракта, routing key и ключ
сообщения. Consumer фиксирует inbox-ключ и локальную команду в одной
транзакции, затем посылает ручное acknowledgement RabbitMQ. Следующая команда
процесса сохраняется до исполнения, поэтому остановка не теряет работу.
Семантика доставки — at-least-once: сообщение может прийти повторно, поэтому
inbox и предметная команда должны быть идемпотентными. Exactly-once здесь не
заявляется.

`FulfillmentProcess` — сохраняемый process manager. Он хранит идентификаторы,
этап и устойчивые work-item'ы, не владеет агрегатами и не является доменной
службой:

```text
OrderPlaced → PaymentAuthorized → InventoryReserved
→ ShipmentCreated → PaymentCaptured → OrderFulfilled
```

При нехватке остатка процесс освобождает реальные резервы, аннулирует платёж
и переводит заказ в `OrderFulfillmentRejected`. При отказе захвата платежа он
сначала отменяет созданное отправление. Политика повторов и dead-letter exchange
принадлежит RabbitMQ, а не доменной модели.

Для каждого подписчика создаются собственные retry- и dead-letter маршруты.
Неудачная доставка публикуется точным ключом, производным от имени очереди;
после TTL она возвращается только в ту же главную очередь. Исходный routing key
остается внутри `NotificationMessage`: технический ключ возврата не подменяет
ключ предметного уведомления.

```bash
uv run pytest
docker compose up -d rabbitmq
uv run python -m north_marketplace prepare --reset --database rabbit-demo.db
uv run python -m north_marketplace relay --database rabbit-demo.db
uv run python -m north_marketplace consume --database rabbit-demo.db
uv run python -m north_marketplace resume --database rabbit-demo.db
uv run python -m north_marketplace state --database rabbit-demo.db
```

Стандартный адрес брокера — `amqp://north:north@localhost:5672/%2F`. Для иной
среды он задаётся переменной `NORTH_RABBITMQ_URL`; CLI, REST и Compose
используют одну и ту же договорённость. Команда `demo` сохраняется как короткий
маршрут, а последовательность `prepare → relay → consume → resume → state`
показывает каждую наблюдаемую границу жизненного цикла.

Параметр `--queue` задаёт durable-очередь процесса; по умолчанию это
`north.fulfillment`. При `--reset` CLI очищает только эту очередь, её retry
очередь и её DLQ вместе с указанной SQLite-базой. Очереди других подписчиков и
общие exchange не изменяются.

Основным является RabbitMQ-путь. Для сопоставления с предыдущей стадией
сохранён явный диагностический режим без брокера:

```bash
uv run python -m north_marketplace demo --transport polling --scenario success --database polling-demo.db --reset
```

`OutboxRelay` публикует только запись, которой владеет по актуальному токену
lease. После publisher confirm запись переводится в `PUBLISHED`; после ошибки
она возвращается в `PENDING`. Устаревший токен получает различимый отказ и не
может изменить состояние чужой записи.

REST сохраняет асинхронное размещение заказа с ответом `202`. Операции
`POST /transport/relay`, `POST /transport/consume`, `POST /processing/resume`
и чтение `/transport/state` служат наблюдаемыми инфраструктурными адаптерами;
они не добавляют предметных правил в HTTP-контроллер.
