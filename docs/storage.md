# Хранение

MVP: SQLite WAL на локальном диске; foreign keys включены; версия схемы и миграции.
Это целевой дизайн, миграций в bootstrap нет.
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

## Очередь и восстановление

Claim через короткую атомарную транзакцию; lease с конечным сроком.
Сеть/модель вне транзакции. Heartbeat только для своего claim.
После сбоя просроченный lease возвращается; новая попытка не затирает прежний outcome.
Сохранение валидной оценки и смена состояния work item атомарны.
Checkpoint и ledger разных БД не составляют общей транзакции: при восстановлении
сначала сверять completed evaluation_key в ledger, затем продолжать граф.

Контент не перезаписывается под старой версией. История условий/резюме сохраняет
смысл принятого решения в пределах разрешённого retention. После удаления текста
оценку без доступного основания помечать expired/unverifiable, не текущей.

## Экспорт

Ссылки, идентификаторы, профиль, вердикт, дата и короткие основания согласно policy.
По умолчанию без полного резюме и полного текста вакансии.
Экспорт пишется в data/exports/, не в Git. Опубликование за рамками MVP.
