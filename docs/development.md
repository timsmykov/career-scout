# Разработка и HH CLI

Python 3.12, uv, Git; Linux/macOS (процессная блокировка через `fcntl.flock`). Из корня:

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
Другие площадки, evaluate и doctor пока не подключены.

| Команда | Действие |
|---|---|
| `migrate` | SQLite v1/v2→v3 с backup, без сети; первое открытие также мигрирует автоматически |
| `plan [--query TEXT]` | Показать план без сети, credentials и открытия БД |
| `collect [--query TEXT]` | Создать run с фиксированными запросами/датами и начать HH сбор |
| `resume RUN_ID` | Продолжить сохранённый план, включая незагруженные карточки |
| `refilter RUN_ID` | Применить текущие правила TOML к full карточкам run без сети, сохранив историю |
| `status RUN_ID` | JSON отчёт без сети |
| `inspect VACANCY_ID [--source hh]` | Состояние, source snapshots, общие проекции и происхождение; default HH |
| `export RUN_ID --output PATH` | Локальный JSON выбранных карточек; `--include-excluded` — все full, решения сохранены |

Без `--query` берётся union четырёх семейств из TOML. `--query` заменяет их;
можно повторять, одинаковые строки объединяются. Настройки ниже — defaults TOML;
CLI их переопределяет. `plan/collect`:
`--days` (7 по умолчанию, положительное целое), `--per-page` (100, диапазон 1–100),
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

При новом discovery изменение listing или истечение refresh-hours делает старую
карточку stale до дочитывания. 404/410 — unavailable; archived сохраняется отдельно.
Обычный экспорт содержит текущие full карточки с KEEP/KEEP_UNKNOWN,
`--include-excluded` — все full, включая `source_payload`; это личный
экспорт с текстами, не публичный отчёт. Старые snapshot доступны через inspect.
Экспорт не перезаписывает существующий файл и публикуется атомарно. Файл остаётся
неизменяемым снимком, но следующий экспорт старого run использует текущие карточки
БД: run не закрепляет content hash навсегда. Возраст карточки виден в detail_fetched_at;
refresh TTL проверяется при discovery, самостоятельного expiry/purge пока нет.

## Границы первого среза

Реализовано: `hh.py`, `storage.py`, `collector.py`, `cli.py`; описание таблиц —
[storage.md](storage.md). Source HH snapshot v1 остаётся immutable
([схема](../schemas/hh-snapshot.schema.json)); export теперь использует
[vacancy-v2.schema.json](../schemas/vacancy-v2.schema.json) с typed salary/experience/
work_formats. Миграция/backfill и примеры — [normalization.md](normalization.md).
Предметные фильтры и история решений реализованы в `filtering.py/search_config.py`.
Raw retention, CSV, стабильный consumer cursor, leases,
прочие адаптеры и расписание впереди. Worker input v1 bootstrap остаётся отложенным.
Графы/модели не импортируются и не вызываются. Зависимости bootstrap оставлены
без изменений; обновление lock — только явным `uv lock` с проверкой.
