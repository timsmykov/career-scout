# Общая вакансия v2 и миграция SQLite v1→v2

SQLite v3 добавила историю фильтров, v4 — query_pagination, текущая v5 —
campaigns/очередь/acquisitions; формат вакансии остаётся v2.
Ниже — контракт нормализации и проверка предшествующей миграции.

`models.py` задаёт Pydantic-контракт всех четырёх источников, `normalization.py`
строит проекцию HH. Маппинг трёх остальных источников реализован
в source_normalization.py и описан в [multi-source.md](multi-source.md). [JSON Schema](../schemas/vacancy-v2.schema.json)
генерируется из модели и проверяется тестом на совпадение.

## Идентичность

Ключ — **(source, source_id)**, source один из `hh/habr/superjob/trudvsem`.
ID не выводится из title, URL или работодателя и не объединяется по похожему тексту.
Одинаковый ID у HH и Habr — разные вакансии, версии и discoveries.

Для HH/Habr/SuperJob helper `source_identity(source, vacancy_id)` сохраняет строковый
provider ID. Для Trudvsem обязателен company code:
`source_identity('trudvsem', 'vacancy-id', company_code='company-code')`.
Source ID — канонический компактный JSON-массив `[company_code,vacancy_id]`,
а не склейка разделителем, который может встретиться в исходных ID. Исходный
payload также сохраняется. Это контракт identity, не утверждение о работающем адаптере.

## Поля

Общая запись содержит source/ID, title/company/URL, полный text и HTML, исходный
`source_payload` и `conditions`, source даты и время дочитывания, detail_status,
source content hash/version, common normalizer version/hash и typed `attributes`.
`full` не означает, что все условия известны: неизвестные поля сохраняются как null.
Никакая вакансия не отбрасывается из-за зарплаты, опыта или формата этим изменением.

### Зарплата

`attributes.salary`: null при отсутствии блока; иначе:

- `amount_from/amount_to`: исходные неотрицательные числа или null, открытые границы
  сохраняются. Явный 0 остаётся 0, отсутствие не превращается в 0.
- `currency`: код в верхнем регистре, HH RUR нормализуется в RUB. Остальные коды,
  включая BYR, не переименовываются с изменением номинала; конвертации валют нет.
- `gross`: true — до налогов, false — после, null — неизвестно. Налогового расчёта нет.
- `period`: hour/shift/day/week/month/year/rotation/service/project или null.
- `payout_frequency`: daily/weekly/twice_monthly/monthly/per_project или null.

Для HH `salary_range` приоритетен целиком над deprecated `salary`: нельзя смешивать
часовую ставку нового блока с месячной оценкой старого. Если modern блока нет,
используется legacy; без явного mode период остаётся null. Частота выплат не задаёт
период ставки. Mode MONTH/HOUR/SHIFT/FLY_IN_FLY_OUT/SERVICE соответствует
month/hour/shift/rotation/service. Другие значения отмечаются как неизвестные.

Некорректные числа/валюта/gross дают null для конкретного поля и `issues`, исходник
сохраняется. Если from > to, обе суммы сохраняются с `salary.reversed_bounds` в
`conflicts`; это противоречие данных, не повод молча переставлять суммы.

### Опыт

`attributes.experience`: null без категории или при неизвестном source ID.
Известная категория имеет category, min/max_years и min/max_inclusive:

| HH category | Общая category | Граница |
|---|---|---|
| noExperience | no_experience | Явно нет опыта, 0–0 |
| between1And3 | one_to_three | Категория 1–3 |
| between3And6 | three_to_six | Категория 3–6 |
| moreThan6 | over_six | Строго больше 6, верхняя граница открыта |

Это границы категории API, а не разбор обязательного требования из текста.
Грейд из количества лет не выводится; фильтры реализованы отдельным этапом
в [filtering.md](filtering.md), вне нормализатора.

### Формат работы

`attributes.work_formats` — список on_site/remote/hybrid/field_work/rotation.
Пустой список означает неизвестно. Несколько допустимых форматов сохраняются
одновременно и сами по себе не являются конфликтом.

HH work_format маппится по явным ID. Legacy schedule.remote и flyInFlyOut дают
remote/rotation только при отсутствии modern форматов. schedule.fullDay/flexible/
shift не определяют место работы и не превращаются в on_site.
Если явные поля/legacy schedule расходятся, сохраняются modern данные и conflict;
ни фильтра, ни догадки по title/description нет.

`attributes.field_sources` содержит пути к исходным полям. `issues` отмечает
неизвестные/некорректные значения, `conflicts` — конкретные противоречия. Source raw
не редактируется, неизвестные ID не теряются. HH справочники/контракт проверены
по [официальному OpenAPI](https://api.hh.ru/openapi/redoc) и
[текущим dictionaries](https://api.hh.ru/dictionaries); справочный corpus XERJ использован
для поиска полей salary_range/mode/frequency/work_format.

## Хеши и история

Существующий `snapshots.payload`, `content_hash`, даты и `hh-1` не переписываются.
Новый `normalized_snapshots` содержит проекцию `(source,source_id,content_hash,
normalizer_version)`; сейчас `common-1`. `normalization_hash` проверяет identity,
source content hash, common version и все attributes, включая происхождение/ошибки.
Время HTTP-проверки не меняет content/normalization hash.

Изменение алгоритма нормализации требует новой версии normalizer и отдельного
backfill; оно не является новым исходным опытом/новой source версией. Текущий read
экспорт использует common-1, raw история доступна через inspect вместе с проекциями.
Экспорт всё ещё читает текущие версии старого run, не закреплённый исторический cutoff.

## Миграция и сохранность

Store при первом открытии v1 автоматически выполняет v1→v2; явная команда без сети:

```sh
docker compose run --rm collector migrate
# Host CLI:
uv run --no-sync python -m career_scout --db data/ledger.sqlite3 migrate
```

Порядок: тот же flock, что у collector → SQLite backup в соседний `backups/` (0600)
→ одна транзакция BEGIN IMMEDIATE → composite keys/FK и backfill всех source snapshots
→ сверка количества/FK/current pointers → commit schema=2. Неизвестная версия
отклоняется. Активный writer не допускает миграцию. Сбой backup запрещает перенос;
ошибка или process death откатывает весь schema/data переход, backup остаётся.
Повторное открытие v2 не создаёт новых версий/backup. Новая пустая БД сразу создаётся
как v2 в одной транзакции; промежуточная v1 не публикуется.

В рабочем volume перед переносом дополнительно сохранён
`/data/backups/before-schema-v2-20261111.sqlite3`; исходный host файл не меняется.
Репетиция на копии и рабочая миграция сверяют **точные строки всех прежних таблиц**:
317 vacancies, 317 immutable snapshots, 634 discoveries, 3 queries, 2 runs, 2 events
и source cooldown. Все сохранённые cursor/счётчики/тексты/хеши остаются прежними.
[Доказательства](references/schema-v2-live-20261111.json).

Старый бинарник v1 не работает с v2 и остановится с unsupported_database_version.
Для восстановления v1 использовать backup с прежним образом в отдельном recovery
volume; не подменять активную рабочую БД и не копировать единственный файл из WAL writer.
Backups содержат личные тексты и остаются вне Git. Общий worker/bootstrap schema v1
сохранён как отложенный контракт; текущий collector экспортирует отдельный v2.

## Интерфейсы

```sh
docker compose run --rm collector inspect VACANCY_ID
# По умолчанию HH. Другие namespaces можно просматривать явно:
docker compose run --rm collector inspect PROVIDER_ID --source habr
docker compose run --rm collector export RUN_ID --output /data/exports/common-v2.json
```

Inspect возвращает immutable `snapshots` и отдельные `normalized_snapshots`.
Export envelope имеет schema_version=2 и normalizer_version=common-1; каждая запись
проверяется общей моделью. Все четыре API-адаптера реализованы; common store принимает их валидные v2
snapshots, а HH transport не запускается для чужого source run. Основной initial
по публикации за14дней описан в [development.md](development.md).

## Производная проверка качества

`data_quality.salary_facts` возвращает известность сумм, единиц и налогового режима,
конфликты и отдельный месячный эквивалент для явно месячной/годовой ставки.
Частота выплат не меняет ставку; часовая/сменная оплата без объёма работы,
неизвестный период и налоги остаются неизвестными. Исходные snapshots не меняются.
Ограниченный `probe` включает безопасный отчёт качества и подпись наблюдаемой
структуры источника. [Контракт и приёмка ядра](core-contracts.md).
