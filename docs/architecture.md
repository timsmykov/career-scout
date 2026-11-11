# Архитектура

## Разделение

1. Collector service: план запросов → список → detail → нормализация → версия.
2. Local filter: только доказуемые исключения, иначе пропуск/unknown.
3. Durable queue: полные согласованные версии ожидают оценки.
4. Evaluation service: фиксированный LangGraph, последовательные профили.
5. Ledger/export: факты обработки, решения и остаток доступны без чтения чата.

Коллектор не получает LLM API. Оценщики не получают ключи job API и права внешней записи.
Разделение обеспечивается зависимостями/передаваемыми объектами, а не только промптом.

## Путь вакансии

```text
query plan → source pages → exact dedup → full detail → normalized snapshot
                                                        ↓
                                               hard-filter result
                                                        ↓
                                             persisted evaluation job
                                                        ↓
validate → profile 1 → profile 2 → profile 3 → profile 4 → aggregate
              │           │           │           │
              └───────────┴── PASS ────┴───────────┘ → ACCEPTED
no PASS + four REJECT → REJECTED
no PASS + any BORDERLINE → NEEDS_CLARIFICATION
no PASS + error/missing result → PENDING
```

Граф не решает, какие API страницы загрузить. Между вакансиями разрешена параллельная
работа; внутри одной цепочки порядок строгий. Не запускать следующие профили
спекулятивно: это нарушает остановку после первого PASS.

## Исполнение

MVP — один процесс CLI и SQLite WAL на локальном диске.
Отдельные файлы ledger.sqlite3 и checkpoints.sqlite3 в игнорируемом data/.
Короткие транзакции claim/save, без транзакции во время HTTP/LLM.
Несколько независимых вакансий могут обрабатываться с ограниченной конкурентностью;
к каждому источнику initially один запрос в полёте и общий бюджет процесса.

LangGraph StateGraph: validate_input → evaluate_profile → route → finalize.
Один параметризованный узел может реализовать четыре логические роли.
В state — IDs, версии, индекс профиля и служебные исходы. Полные тексты извлекаются
из ledger и передаются модели целиком, но не копируются в длинную message history.

Persistent checkpointer хранит ход исполнения. Ledger — авторитетный реестр
результатов. Checkpoint не даёт exactly-once внешнему LLM: при падении после ответа,
но до фиксации возможен повтор и повторная стоимость. Результат сохраняется
идемпотентно по evaluation_key, попытки учитываются отдельно.

## Переход к нескольким процессам

Не добавлять брокер заранее. Сначала измерить очередь/SQLite contention.
Если потребуется несколько серверов — ADR о PostgreSQL, общих source budgets,
leases, checkpointer и миграции данных. Нельзя масштабировать HTTP workers,
умножив лимиты одного app/IP.

## Состояния

collection_status (complete/partial/blocked/failed) отдельно от detail_status
(full/partial/missing/stale/archived/unavailable), filter_result и matching status.
Успех сохранения списка не означает полноту карточек или оценки.
