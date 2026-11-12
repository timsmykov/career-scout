# Хранение

## Реализовано: schema v5

SQLite WAL, foreign_keys=ON, PRAGMA user_version=5. Пустая БД создаётся в одной
транзакции; v1/v2/v3/v4 переносятся с обязательным backup, неизвестная версия отклоняется.
Ключ `(source,source_id)` поддерживает четыре пространства идентификаторов.

| Таблица | Ключ / назначение |
|---|---|
| campaigns | Общий initial/new проход и неизменяемый набор source plans |
| campaign_runs | Связь campaign+source с одним дочерним run |
| campaign_items | Уникальные ID прохода с origin new/known/backlog |
| acquisitions | Первое сохранённое описание ID, campaign/run/hash; атомарно со snapshot |
| detail_failures | Отложенные попытки отдельной карточки и состояние failed |
| runs | Run ID, source, immutable plan, статус и счётчики |
| queries | Query/window/run, next_page и полнота |
| query_pagination | Query ID + фактический native page size, фиксируется атомарно со страницей |
| vacancies | source+source_id, listing, source content pointer, lifecycle |
| snapshots | source+source_id+content_hash, immutable исходный payload/HTML/text |
| normalized_snapshots | source+source_id+content_hash+normalizer_version, общий v2 payload |
| discoveries | run+query+source+source_id; FK запрещают смешать source или чужой query |
| events | Технические события без auth/body |
| source_state | Независимый cooldown каждого source |
| filter_policies | Hash значений политики + версия движка, immutable definition |
| run_filter_policies | Активная политика run; первоначальный plan не меняется |
| filter_decisions | Source version+normalizer+policy, решение с причинами/цитатами |

[Миграция и нормализация](normalization.md): все исходные snapshot строки/хеши,
run/query/event/cooldown данные сохранены; raw и normalized версии раздельны.
После успешной миграции старые временные таблицы удаляются в той же транзакции,
backup старой схемы остаётся. V2→v3 только добавляет три таблицы фильтров;
существующие строки не переписываются. V3→v4 добавляет query_pagination,
все прежние таблицы/строки сохраняются. V4→v5 только добавляет таблицы campaigns,
campaign_runs, campaign_items, acquisitions и detail_failures. Первое сохранение
описания и acquisition коммитятся вместе; последующие initial/new его не меняют. [Контракт отбора](filtering.md). Ошибка или авария до commit оставляет старую схему целиком.

Один writer на ledger через flock, сеть вне транзакции. Cursor/discoveries и
snapshot/current pointer/projection записываются атомарно. Нормализация сама по себе
не создаёт новую source content version. Экспорт читает common-1 full проекции;
старые source snapshots доступны отдельно через inspect.

Read export использует один SQLite snapshot, но старый run по-прежнему читает
текущие версии. Возраст карточки виден в detail_fetched_at; expiry/purge/retention,
CSV, неизменяемый version set по run и consumer cursor ещё не реализованы.

## Целевая модель общего коллектора

MVP: SQLite WAL на локальном диске; foreign keys включены; версия схемы и миграции.
Таблицы ниже — будущая общая модель, а не буквальные имена текущей schema v5.
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
