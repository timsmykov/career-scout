# Career Scout

Сервис сбора, систематизации и хранения вакансий для последующего анализа.

**Статус: включён сбор четырёх источников.** HH, «Работа России», Habr и SuperJob
сохраняют полные карточки в общую SQLite с пагинацией, версиями и продолжением после сбоя.
Владелец подтвердил наличие разрешений на хранение вакансий 11.11.2026. [Точный статус, запуск и ограничения](docs/multi-source.md).

Общий формат vacancy/export v2; SQLite schema5 мигрирует прежнюю базу с backup.
Поиск и фильтры — [configs/search.toml](configs/search.toml): IT/финтех/ИИ/ML,
стажёр/junior/middle, без опыта/1–3 года. Unknown/conflict сохраняются, зарплата
не ограничивает отбор. Реализованы профильные очереди с native Decisions и оценкой через подписку Codex;
отклики не отправляются. [Анализ и ручной запуск](docs/evaluation.md).

## Что строим

Текущий этап: сбор HH, Хабр Карьеры, «Работы России» и SuperJob, полные описания,
нормализация условий, версии и дедупликация, надёжное хранение и продолжение после
сбоев. Выход — готовый набор вакансий с происхождением и локальным экспортом.
Анализ готовых карточек развивается параллельно сбору: Decisions выбирает один или
два профиля, четыре профильных воркера работают одновременно батчами до10карточек.
У каждого батча одно полное резюме. Отказ выбранных оценщиков не означает отказ
всех четырёх. [Контракт и калибровка100](docs/routing-batches.md).
Legacy first-PASS сохранён для прежних запусков.

Четыре выбранных источника имеют разный статус доступа. У Хабра проверен
недокументированный frontend JSON и HTML, а официальная API-справка относится к CRM
работодателей. SuperJob зарегистрирован; поиск/detail и постоянное сохранение включены.

## Основа сборщика

Основной референс — [ru-it-jobs-radar](https://github.com/alekseevpavel04/ru-it-jobs-radar)
(MIT): отдельные адаптеры, контракт входных данных, окна выдачи, повторные запросы,
контроль недочитанных карточек. Берём ограниченные механизмы, без переноса
аналитического стека. HH-клиент проектируется по официальному OpenAPI.

В этом bootstrap чужой исполняемый код не скопирован.
[Решение и точные исходники](docs/reuse.md).

## Начать

Нужны Python 3.12 и [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --group dev
uv run --no-sync python scripts/check_repository.py
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -m "not live"
```

Проверки выполняются без сети к job-площадкам и LLM. Для HH заполните локальный
`.env` по `.env.example` (application token и контактный User-Agent; client ID/secret
нужны для получения нового токена). Команды из корня репозитория:

```sh
# План без сети и без открытия БД
uv run --no-sync python -m career_scout plan --source all --days 14
uv run --no-sync python -m career_scout plan --query "бизнес аналитик" --days 7
# Первичный обход вакансий за14дней; сохраняет общий campaign_id
uv run --no-sync python -m career_scout initial --source all --days 14 --progress
# Следующий ручной проход: поиск всех ID, описания только новых
uv run --no-sync python -m career_scout new --source all --days 14 --progress
# Без сохранения вакансий, включая SuperJob:
uv run --no-sync python -m career_scout probe --source all --query аналитик --requests 3
# CAMPAIGN_ID берётся из результата initial/new; resume продолжает все площадки
uv run --no-sync python -m career_scout resume CAMPAIGN_ID --progress
uv run --no-sync python -m career_scout status CAMPAIGN_ID
uv run --no-sync python -m career_scout inspect VACANCY_ID
uv run --no-sync python -m career_scout export CAMPAIGN_ID --output data/exports/run.json
```

`initial` и ручной `new` используют последние 14 дней по дате публикации.
Каждый новый проход фиксирует своё окно; resume продолжает прежнее. Читаются все доступные
страницы настроенных запросов; даты обновления не подменяют публикацию. Записи
старше окна и с неизвестной датой видны отдельно и не входят в свежую выгрузку. Уже сохранённые описания не обновляются; каждый источник проходит свою
выдачу целиком без квот на число карточек. `new` экспортирует только новые описания,
включая отдельно учтённый дочитанный хвост. Запуски ручные, расписания нет.

`partial` и exit code 2 означают незавершённый сбор; остаток виден в отчёте.
Полный справочник аргументов и восстановление: [development.md](docs/development.md).
CLI читает `.env`, [рабочий TOML](configs/search.toml) и аргументы. Без `--query`
используются четыре поисковых семейства. [Правила отбора и refilter](docs/filtering.md).
[Проверенный HH-срез и ограничения](docs/collector-readiness.md).

## Docker

Рабочий контейнер с постоянным volume `career-scout-data`:

```sh
docker volume create career-scout-data
docker compose build
docker compose run --rm collector status CAMPAIGN_ID
docker compose run --rm collector resume CAMPAIGN_ID --progress
```

SQLite сохраняется в `/data/ledger.sqlite3`, `.env` монтируется read-only;
контейнер можно пересоздать. [Устройство, миграция и проверки](docs/docker.md).

## Анализ через подписку Codex

Анализатор запускается на хосте с установленным Codex CLI и существующим входом
через ChatGPT. Проверена CLI0.166.0; ключ model API не требуется. `gpt-6-luna`/`max`
вызывается отдельно для каждого профильного батча. Очереди, сохранение и повторы
выполняет код. Категоризация использует отдельный native Decisions API; оценщики
по-прежнему работают через подписку.

Для нового режима используйте [analysis.routed.example.json](configs/analysis.routed.example.json)
и [руководство](docs/routing-batches.md). Калибровка на100вакансиях: основной класс98/100. Уверенный один профиль достаточен;
рабочий gap≤0.10 подключает второго только при близком результате.

Для legacy нужны четыре полных приватных снимка резюме и собственный JSON по
[analysis.example.json](configs/analysis.example.json). Пути в JSON считаются
относительно его каталога. В примере вызовы выключены; рабочий конфиг фиксирует
подтверждённый порядок и разрешение модельных вызовов.

```sh
# Фиксация входов: модель не вызывается, частичный campaign допускается
uv run --no-sync python -m career_scout.analysis prepare --config private/analysis.json --limit 20 --max-calls 80
uv run --no-sync python -m career_scout.analysis run RUN_ID --db data/analysis.sqlite3
uv run --no-sync python -m career_scout.analysis status RUN_ID --db data/analysis.sqlite3
uv run --no-sync python -m career_scout.analysis resume RUN_ID --db data/analysis.sqlite3
uv run --no-sync python -m career_scout.analysis export RUN_ID --db data/analysis.sqlite3 --output data/analysis-results.json
```

Бюджет вызовов сохраняется до обращения к Codex. Подробности об ошибках, явном
повторе и версиях: [evaluation.md](docs/evaluation.md). SQLite коллектора не меняется.
Состояние графа и проверенные ответы сохраняются в отдельной `analysis.sqlite3`.

## Читать по порядку

1. [Задача и границы](docs/product.md).
2. [Архитектура](docs/architecture.md).
3. [Сбор и полнота](docs/collector.md), [контракты площадок](docs/sources/README.md).
4. [Фильтры](docs/filtering.md), [хранение](docs/storage.md).
5. [Хранение](docs/storage.md), [эксплуатация](docs/operations.md).
6. [Разработка](docs/development.md), [критерии приёмки](docs/acceptance.md).

[Полный указатель](docs/README.md). Старая [схема будущего анализа](docs/assets/funnel.png)
сохранена как исторический материал; текущий поток описан в архитектуре.

## Данные

Реальные резюме, тексты вакансий, ключи, cookies и рабочие БД не коммитятся.
В examples/ только вымышленные примеры. Пример проекта выключает все внешние
вызовы. Модели и резюме не требуются для приёмки текущего коллектора.

Лицензия для собственного кода проекта пока не выбрана. Права третьих сторон
фиксируются отдельно в [NOTICE](NOTICE.md).

[Проверка четырёх площадок и остаток реализации](docs/collector-readiness.md).

Разработка: [полный roadmap](docs/roadmap.md) · [что переносим из radar](docs/component-adaptation.md) · [backlog](docs/backlog.json).
