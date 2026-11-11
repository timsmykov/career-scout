# Career Scout

Сервис сбора, систематизации и хранения вакансий для последующего анализа.

**Статус: включён сбор четырёх источников.** HH, «Работа России», Habr и SuperJob
сохраняют полные карточки в общую SQLite с пагинацией, версиями и продолжением после сбоя.
Владелец подтвердил наличие разрешений на хранение вакансий 11.11.2026. [Точный статус, запуск и ограничения](docs/multi-source.md).

Общий формат vacancy/export v2; SQLite schema4 мигрирует прежнюю базу с backup.
Поиск и фильтры — [configs/search.toml](configs/search.toml): IT/финтех/ИИ/ML,
стажёр/junior/middle, без опыта/1–3 года. Unknown/conflict сохраняются, зарплата
не ограничивает отбор. LangGraph и анализ по резюме отложены, отклики не отправляются.

## Что строим

Текущий этап: сбор HH, Хабр Карьеры, «Работы России» и SuperJob, полные описания,
нормализация условий, версии и дедупликация, надёжное хранение и продолжение после
сбоев. Выход — готовый набор вакансий с происхождением и локальным экспортом.
LangGraph, анализ по резюме и настройка моделей отложены до приёмки коллектора.

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
uv run --no-sync python -m career_scout plan --source all
uv run --no-sync python -m career_scout plan --query "бизнес аналитик" --days 7
# Явный сетевой сбор: конечный бюджет на весь запуск, включая auth и повторы
uv run --no-sync python -m career_scout collect --source all --days 7 --requests 50
# Без сохранения вакансий, включая SuperJob:
uv run --no-sync python -m career_scout probe --source all --query аналитик --requests 3
# RUN_ID берётся из JSON результата collect
uv run --no-sync python -m career_scout resume RUN_ID --requests 50
uv run --no-sync python -m career_scout status RUN_ID
uv run --no-sync python -m career_scout inspect VACANCY_ID
uv run --no-sync python -m career_scout export RUN_ID --output data/exports/run.json
```

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
docker compose run --rm collector status RUN_ID
docker compose run --rm collector resume RUN_ID --requests 50
```

SQLite сохраняется в `/data/ledger.sqlite3`, `.env` монтируется read-only;
контейнер можно пересоздать. [Устройство, миграция и проверки](docs/docker.md).

## Читать по порядку

1. [Задача и границы](docs/product.md).
2. [Архитектура](docs/architecture.md).
3. [Сбор и полнота](docs/collector.md), [контракты площадок](docs/sources/README.md).
4. [Фильтры](docs/filtering.md), [хранение](docs/storage.md).
5. [Хранение](docs/storage.md), [эксплуатация](docs/operations.md).
6. [Разработка](docs/development.md), [критерии приёмки](docs/acceptance.md).

[Полный указатель](docs/README.md). Старая [схема будущего анализа](docs/assets/funnel.png)
сохранена как отложенный материал; текущий поток показан в архитектуре.

## Данные

Реальные резюме, тексты вакансий, ключи, cookies и рабочие БД не коммитятся.
В examples/ только вымышленные примеры. Пример проекта выключает все внешние
вызовы. Модели и резюме не требуются для приёмки текущего коллектора.

Лицензия для собственного кода проекта пока не выбрана. Права третьих сторон
фиксируются отдельно в [NOTICE](NOTICE.md).

[Проверка четырёх площадок и остаток реализации](docs/collector-readiness.md).

Разработка: [полный roadmap](docs/roadmap.md) · [что переносим из radar](docs/component-adaptation.md) · [backlog](docs/backlog.json).
