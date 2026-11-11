# Хранение

## Реализовано: schema v2

SQLite WAL, foreign_keys=ON, PRAGMA user_version=2. Пустая БД создаётся в одной
транзакции; v1 переносится с обязательным backup, неизвестная версия отклоняется.
Ключ `(source,source_id)` поддерживает четыре пространства идентификаторов.

| Таблица | Ключ / назначение |
|---|---|
| runs | Run ID, source, immutable plan, статус и счётчики |
| queries | Query/window/run, next_page и полнота |
| vacancies | source+source_id, listing, source content pointer, lifecycle |
| snapshots | source+source_id+content_hash, immutable исходный payload/HTML/text |
| normalized_snapshots | source+source_id+content_hash+normalizer_version, общий v2 payload |
| discoveries | run+query+source+source_id; FK запрещают смешать source или чужой query |
| events | Технические события без auth/body |
| source_state | Независимый cooldown каждого source |

[Миграция и нормализация](normalization.md): все исходные snapshot строки/хеши,
run/query/event/cooldown данные сохранены; raw и normalized версии раздельны.
После успешной миграции старые временные таблицы удаляются в той же транзакции,
backup v1 остаётся. Ошибка или авария до commit оставляет старую схему целиком.

Один writer на ledger через flock, сеть вне транзакции. Cursor/discoveries и
snapshot/current pointer/projection записываются атомарно. Нормализация сама по себе
не создаёт новую source content version. Экспорт читает common-1 full проекции;
старые source snapshots доступны отдельно через inspect.

Read export использует один SQLite snapshot, но старый run по-прежнему читает
текущие версии. Возраст карточки виден в detail_fetched_at; expiry/purge/retention,
CSV, неизменяемый version set по run и consumer cursor ещё не реализованы.

## Целевая модель общего коллектора

MVP: SQLite WAL на локальном диске; foreign keys включены; версия схемы и миграции.
Таблицы ниже — будущая общая модель, а не буквальные имена текущей schema v2.
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
