# Хранение

## Реализовано для HH

`storage.py`: SQLite WAL, foreign_keys=ON, PRAGMA user_version=1. Пустая БД
создаётся в транзакции; незнакомая версия отклоняется. Ключи таблиц HH-specific.

| Таблица v1 | Назначение |
|---|---|
| runs | Immutable plan, статус, накопленные request reservations, pages/rejected |
| queries | Query + date window, next_page, pending/complete/split, truncated |
| vacancies | HH ID, последний listing/hash, current snapshot hash, detail state/время |
| snapshots | HH ID + content hash, полный исходный detail JSON, HTML, text и условия |
| discoveries | Run + query/window + HH ID, без дублей внутри этой тройки |
| events | Фиксированные коды rejected rows и ошибок источника, без HTTP body/headers |
| source_state | Сохранённый HH cooldown для всех запусков в этой БД |

Один collector на БД через flock. Сеть вне транзакций; cursor и discoveries
коммитятся вместе, snapshot и текущий указатель также атомарны. Версия содержимого
не меняется от локального времени/счётчиков. Повторная проверка того же содержания
обновляет время в vacancies; snapshots сохраняют первое наблюдение версии.
Резервация запроса записывается до HTTP: авария может завысить счётчик на одну
неотправленную резервацию, но не потеряет уже начатую попытку.

Read export использует одну SQLite-транзакцию. Это текущие full версии (не stale,
archived или unavailable), вместе с последним detail_fetched_at. Старый run не
фиксирует навсегда исторический набор версий. Нет автоматического expiry/purge,
lease-очереди и multi-source identity; JSON содержит исходный payload и хранится
локально. [Команды и ограничения](development.md).

## Целевая модель общего коллектора

MVP: SQLite WAL на локальном диске; foreign keys включены; версия схемы и миграции.
Таблицы ниже — будущая общая модель, а не текущая HH schema v1.
Текущий этап реализует только source/run/query/vacancy/discovery/filter/work/event
сущности. resumes/evaluations/routing и model checkpoints отложены.

| Таблица | Ключ / назначение |
|---|---|
| sources | source, capabilities, access/retention policy version |
| collection_runs | run_id, immutable plan/config version, status/counters |
| query_progress | run_id+source+query+window, cursor, completeness, watermark |
| vacancies | source+source_id, current snapshot pointer, lifecycle |
| vacancy_snapshots | source+source_id+content_hash, normalized data, detail_status |
| raw_fetches | fetch ID/hash, endpoint, received_at, source policy/expiry; без auth headers |
| discoveries | vacancy identity + run/query, исходное семейство обнаружения |
| resumes | profile_id+resume_hash, local path/version, imported_at |
| evaluations | unique evaluation_key, validated verdict/evidence/model versions |
| evaluation_attempts | attempt_id, evaluation_key, error class, usage, timing |
| routing_runs | route_key, profile order version, next index, aggregate |
| work_items | unique kind+input version key, status, lease_owner/expires_at, attempts |
| filter_decisions | vacancy version+filter version, reason/evidence |
| run_events | append-only технические переходы без raw prompt/credentials |

Полный raw payload хранится только если разрешён политикой источника. Настройка
retention_mode=disabled означает запрет записи и передачи текста в model queue,
а не попытку сохранить его обходным производным форматом. TTL распространяется на
копии в очереди, checkpoint, exports, raw logs и резервных копиях согласно политике.

Canonical identity не зависит от URL-параметров. Для Trudvsem ID может требовать
companycode+id: фиксируется адаптером, не объединять по одному непроверенному id.
Timezone: UTC внутри, исходная точность даты сохраняется отдельно.
published_at / source_updated_at / fetched_at / detail_fetched_at различаются.
Нельзя превращать дату поднятия в дату первичного размещения.

## Целевая очередь с leases (ещё не реализована)

Claim через короткую атомарную транзакцию; lease с конечным сроком.
Сеть/модель вне транзакции. Heartbeat только для своего claim.
После сбоя просроченный lease возвращается; новая попытка не затирает прежний outcome.
Сохранение валидной оценки и смена состояния work item атомарны.
Checkpoint и ledger разных БД не составляют общей транзакции: при восстановлении
сначала сверять completed evaluation_key в ledger, затем продолжать граф.

Контент не перезаписывается под старой версией. История условий/резюме сохраняет
смысл принятого решения в пределах разрешённого retention. После удаления текста
оценку без доступного основания помечать expired/unverifiable, не текущей.

## Целевой экспорт общего MVP

Ссылки, идентификаторы, профиль, вердикт, дата и короткие основания согласно policy.
По умолчанию без полного резюме и полного текста вакансии.
Экспорт пишется в data/exports/, не в Git. Опубликование за рамками MVP.
