# Разработка и multi-source CLI

Python 3.12, uv, Git; Linux (процессная блокировка через `fcntl.flock`). Из корня:

```sh
uv sync --locked --group dev
uv run --no-sync python scripts/check_repository.py
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -m "not live"
uv build
```

Обычные проверки не используют credentials, job API или LLM. HTTP-тесты работают
через HTTPX MockTransport, хранилище — настоящая временная SQLite. Отдельный тест
обрывает дочитывающий subprocess через `os._exit`, затем продолжает сбор из БД.
`scripts/check_repository.py` дополнительно проверяет будущие межплощадочные схемы,
вымышленные примеры, ссылки и выключенные TOML-шаблоны.

## Действующий CLI

Общая форма: `uv run --no-sync python -m career_scout [--db PATH] [--env PATH] [--config PATH] COMMAND`.
Общие аргументы идут **до** команды. Опциональный `--token-file PATH` включает
отдельный writable cache токена для read-only исходного env; [Docker](docker.md). По умолчанию `data/ledger.sqlite3` и `.env`.
Рабочий config — `configs/search.toml`; [семейства и фильтры](filtering.md).
HH/Trudvsem/Habr/SuperJob подключены; evaluate и doctor пока нет.
`--source hh|trudvsem|habr|superjob|all` доступен у plan/collect/probe; default hh.
Бюджет batch задаётся на источник. [Native контракты и статус](multi-source.md).

| Команда | Действие |
|---|---|
| `migrate` | SQLite v1/v2/v3/v4→v5 с backup, без сети; первое открытие также мигрирует автоматически |
| `plan [--query TEXT]` | Показать план без сети, credentials и открытия БД |
| `initial [--source all]` | Первичный обход за14дней по публикации; только неизвестные описания |
| `new [--source all]` | Ручной повтор поиска; отдельный экспорт только новых описаний |
| `collect [--query TEXT]` | Создать run с фиксированными запросами/датами и начать сбор выбранного источника |
| `probe [--source all]` | Search+detail в памяти без БД и сохранения текста; используется также для SuperJob |
| `resume RUN_ID` | Продолжить сохранённый план, включая незагруженные карточки |
| `refilter RUN_ID` | Применить текущие правила TOML к full карточкам run без сети, сохранив историю |
| `status RUN_ID` | JSON отчёт без сети |
| `inspect VACANCY_ID [--source hh]` | Состояние, source snapshots, общие проекции и происхождение; default HH |
| `export RUN_ID --output PATH` | Локальный JSON выбранных карточек; `--include-excluded` — все full, решения сохранены |

Без `--query` используются ваши families из TOML; публичный placeholder
запускать нельзя. `--query` заменяет их;
можно повторять, одинаковые строки объединяются. Настройки ниже — defaults TOML;
CLI их переопределяет. `plan/collect`:
`--days` (14 по умолчанию, положительное целое), `--per-page` (100, диапазон 1–100),
`--refresh-hours` (24, конечное неотрицательное число). На момент создания run
фиксируется UTC-окно `[now-days, now]`. Это lookback, а не расписание.

`plan/collect/resume`: `--requests` (50, положительное целое), `--details` (30, целое ≥0),
`--interval` (1 секунда, конечное число ≥0), `--retries` (2, диапазон 0–5).
В новом run настройки фиксируются в плане; resume не перечитывает TOML.
Это бюджет **текущего вызова**: новый `resume` получает новый бюджет, накопленные
резервации остаются в отчёте. Token acquire, повторные HTTP и проверочные запросы
перед разбиением окна также расходуют бюджет. `--details 0` сохраняет только списки,
полные карточки остаются отложенными. После исчерпания detail budget поиск может
продолжаться до общего request budget; complete при недочитанных карточках запрещён.
Интервал 1 секунда — наша настройка, а не обещанная HH квота.

```sh
uv run --no-sync python -m career_scout plan --query "бизнес аналитик" --days 7
uv run --no-sync python -m career_scout collect --query "бизнес аналитик" --per-page 2 --requests 1
# Скопировать run_id из результата collect
uv run --no-sync python -m career_scout resume RUN_ID --requests 20 --details 15
uv run --no-sync python -m career_scout status RUN_ID
uv run --no-sync python -m career_scout export RUN_ID --output data/exports/hh-run.json
```

Коды завершения: **0** — complete или успешная локальная команда; **2** — отчёт
partial/blocked (также argparse при неверных аргументах); **3** — конфигурация,
неизвестный run/ID или локальное хранилище. Не считать exit 2 отсутствием вакансий.
Для планового continuation использовать `run_id`, а не повторять `collect`.

## Основной путь: общий проход initial/new

```sh
docker compose run --rm collector initial --source all --days 14 --progress
docker compose run --rm collector status CAMPAIGN_ID
docker compose run --rm collector resume CAMPAIGN_ID --progress
docker compose run --rm collector export CAMPAIGN_ID --output /data/exports/initial.json
# На следующий день — вручную:
docker compose run --rm collector new --source all --days 14 --progress
docker compose run --rm collector export NEW_CAMPAIGN_ID --output /data/exports/new.json
```

У initial/new default source=all. `--query` (повторяемый) заменяет семейства,
`--per-page` задаёт запрошенный размер (1–100, источник может уменьшить).
`--days` (по умолчанию14) задаёт окно публикации нового initial или new.
`--chunk-requests` (200) ограничивает одну порцию HTTP, включая повторы и auth;
порции повторяются до завершения всех площадок, без общего лимита числа карточек.
`--max-rounds N` явно ограничивает число порций на источник за вызов (для проверки/
ручной остановки); такой результат остаётся partial. `--progress` выдаёт NDJSON
при создании campaign, каждые30сек запросов и по итогам порций; финальный JSON — отчёт.
`resume CAMPAIGN_ID` и resume дочернего RUN_ID продолжают **всю** campaign;
план и курсоры берутся из SQLite без перечитывания TOML. Для legacy run IDs
сохраняется прежний одиночный collect/resume; `--refresh-hours` относится к нему; --days у initial/new задаёт их новое окно.

Initial и new используют `--days 14` по умолчанию: publication_start=T0−14дней,
publication_end=T0. Параметр меняет только создаваемый проход; resume сохраняет прежний
диапазон. Старый широкий initial остановлен, его план не переписывается.
HH/SuperJob получают границы публикации в API. Trudvsem получает окно изменений
и дополнительно проверяется по creation-date; Habr читает all_visible и
проверяется по publishedDate/JobPosting.datePosted. Старая публикация с новой
датой изменения не проходит. Источник без точной даты сохраняется отдельно
как unknown_publication_date и не попадает в свежий экспорт, даже с
--include-excluded. Неполная дата на нижней границе также unknown. Уже сохранённые
старые карточки остаются в архиве без изменений. Следующий ручной new получает
новое скользящее14дневное окно и пропускает известные ID. Поэтому старые объявления,
пропущенные первичным проходом по возрасту, не загружаются в ежедневном режиме.
Trudvsem использует страницы не больше20 объектов.
На live проверке100 объектов не ответили за45сек;20 объектов с явными датами
вернулись200 за8,3сек. Это размер страницы, не ограничение общего числа вакансий.
При cap HH2000/SJ500 сохраняются дочерние окна; неограниченная старая часть делится
на последние30дней и более раннюю часть, затем конечные окна делятся пополам.
Первый известный ID не останавливает страницу/запрос. Новая вакансия, появившаяся
после начала initial, попадёт в следующий new; даты последнего полного прохода
не служат исключающим watermark. Сдвиги живой выдачи остаются ограничением площадок.

Сохранённые описания не загружаются повторно даже при изменении listing/истечении
TTL. Старые listing/снимки не меняются. Trudvsem отдаёт полный объект в списке:
если он проходит тот же нормализатор, сохраняем его без дополнительного detail GET;
при неполном объекте используется отдельный endpoint. В new импортируется и старый
незавершённый хвост. Отчёт различает new/known/backlog ID и new_descriptions.
Экспорт initial содержит только подтверждённые публикации из14дневного окна, экспорт new — только
впервые сохранённые в этой campaign описания (в том числе backlog). Для всего
найденного каталога daily используйте `export ... --include-known`.
`--include-excluded` добавляет full записи, исключённые предметным фильтром.

Для плотного Trudvsem запроса известный total>300 запускает временные split;
дочерние окна читаются со страницей5, без перечитывания всего диапазона родителя.
Это цель размера окна, не общий cap: все подокна остаются в плане, а после
минимального интервала1сек чтение продолжается нативными страницами. Публикация
проверяется по исходному окну campaign. Новый CLI-флаг для этого не нужен.

Ошибка отдельной карточки не блокирует остальные: до3 попыток с отложенными
повторами60/120сек, затем failed в SQLite. Без успешного дочитывания complete
невозможен. `resume ... --retry-failed` явно возвращает такие карточки в очередь.
404/410 сохраняется как unavailable. 401/403 останавливает источник; новая ручная
resume может проверить исправленный доступ. 429/квотный503 сохраняет общий cooldown.
После трёх ответов rate limit за один вызов источник останавливается до ручного
resume, чтобы не ждать квоту бесконечно. У Trudvsem таймаут/5xx поиска дополнительно уменьшает размер страницы:
20→10→5→2→1. Запрос с теми же текстом и датами сохраняется как новый query
с cursor=0; прежний query получает статус replanned и сохраняет исходный cursor.
Сдвиг старого cursor в другой размер страницы не используется: порядок выдачи
при смене limit не гарантирован. Уже сохранённые описания не загружаются снова.
Переход query/cursor/event атомарный; счётчик pagination_replans виден в status.
При размере1 ошибка завершает источник с partial. 401/403/429 и ошибки схемы
не запускают этот механизм. У остальных источников ошибки поиска после
ограниченных HTTP retries останавливают источник до ручного resume; другие продолжают. Partial/truncated не означает пустую выдачу.

## Credentials и границы

Скопировать `.env.example` в `.env` только в новом окружении; существующий файл
не перезаписывать. Заполнить `HH_USER_AGENT` с настоящим контактом и application
`HH_ACCESS_TOKEN`. Для получения/замены application token используются
`HH_CLIENT_ID/HH_CLIENT_SECRET`, POST `/token` с `client_credentials`.
401 допускает одну замену на invocation, повторный 401 завершает источник.
403 не обходится; 429 сохраняет cooldown по Retry-After (fallback 60 секунд).
Cooldown общий для HH-запусков **в одной БД**, не для независимых БД/других программ.
Параллельные независимые базы для обхода ограничения использовать нельзя.
По умолчанию токен обновляется атомарно в `.env`, права 0600, прочие ключи
сохраняются. С `--token-file` обновляется только cache (0600), исходный env
остаётся read-only; cache scoped по application ID, переживает Docker recreation.
Тела ошибок HTTP, headers и секреты в CLI не печатаются. Request timeout 25 секунд.

## Данные и восстановление

Страница и cursor коммитятся вместе. Сначала дочитывается уже известный pending
хвост, затем берётся следующая страница. Авария до commit повторит страницу;
после commit повторит только незавершённый detail. Один collector на БД; ОС снимет
flock после аварии. Сетевые запросы выполняются вне SQLite-транзакций.

HH выдача свыше 2000 делится пополам по UTC-датам, с общей граничной секундой.
Окна/прогресс сохраняются, пересечения дедуплицируются по HH ID. Если даже окно
в одну секунду переполнено, доступная выдача сохраняется, `truncated_queries > 0`
оставляет run partial. Изменение выдачи во время прохода всё ещё может смещать
страницы: гарантии полного охвата меняющегося рынка здесь нет.

Исходный detail JSON и HTML сохраняются локально вместе с полным очищенным текстом,
без обрезки и подмены snippet. Зарплата, gross/net, опыт и прочие условия сохраняют
значения источника/null. Изменение текста/значимых условий создаёт новый content hash;
изменение счётчиков просмотров не создаёт версию. HH `published_at`, `created_at`,
`updated_at` и локальный `detail_fetched_at` различаются. Source updated не выдумывается.

Только в legacy collect: при новом discovery изменение listing или истечение refresh-hours делает старую
карточку stale до дочитывания. 404/410 — unavailable; archived сохраняется отдельно.
Обычный экспорт содержит текущие full карточки с KEEP/KEEP_UNKNOWN,
`--include-excluded` — все full, включая `source_payload`; это личный
экспорт с текстами, не публичный отчёт. Старые snapshot доступны через inspect.
Экспорт не перезаписывает существующий файл и публикуется атомарно. Файл остаётся
неизменяемым снимком, но следующий экспорт старого run использует текущие карточки
БД: run не закрепляет content hash навсегда. Возраст карточки виден в detail_fetched_at;
refresh TTL проверяется при discovery, самостоятельного expiry/purge пока нет.

## Границы реализации

Реализовано: `hh.py`, `adapters.py`, `transport.py`, `source_normalization.py`,
`storage.py`, `collector.py`, `cli.py`; описание таблиц —
[storage.md](storage.md). Source HH snapshot v1 остаётся immutable
([схема](../schemas/hh-snapshot.schema.json)); export теперь использует
[vacancy-v2.schema.json](../schemas/vacancy-v2.schema.json) с typed salary/experience/
work_formats. Миграция/backfill и примеры — [normalization.md](normalization.md).
Предметные фильтры и история решений реализованы в `filtering.py/search_config.py`.
CSV, стабильный consumer cursor и leases остаются будущими расширениями.
Refresh/reconciliation, raw retention и расписание не входят в текущий запрос владельца. Worker input v1 bootstrap остаётся отложенным.
Графы/модели не импортируются и не вызываются. Зависимости bootstrap оставлены
без изменений; обновление lock — только явным `uv lock` с проверкой.
