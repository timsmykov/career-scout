# Docker: коллектор и постоянная SQLite

Реализация остаётся batch CLI: контейнер запускается на одну команду, завершает её
и останавливается. `restart: no`; нет расписания, фонового поиска и HTTP-сервера.
LangGraph и агенты не используются. Исходный Python CLI также остаётся доступен.

## Подготовка

Из корня проекта, с уже заполненным локальным `.env`:

```sh
docker volume create career-scout-data
docker compose build
```

Образ `career-scout:local` собирается в два этапа: Python 3.12 и uv 0.13.6 по
зафиксированным digest, установка по `uv.lock --locked --no-dev --no-editable`.
Финальный образ содержит установленный пакет и окружение, без исходных секретов,
рабочих БД, экспортов, Git и host `.venv`. `.dockerignore` задаёт allowlist.
Bootstrap-зависимости проекта оставлены прежними; код моделей не импортируется.

Volume объявлен external: Compose использует заранее созданный `career-scout-data`
и не управляет его удалением. Удаление/пересоздание контейнера не удаляет данные.
Сам volume не удалять. Это сохранность при пересоздании контейнера, не отдельная
резервная копия при потере сервера.

## Запуск

```sh
docker compose run --rm collector plan --query "бизнес аналитик" --days 7
docker compose run --rm collector collect --query "бизнес аналитик" --requests 50
docker compose run --rm collector resume RUN_ID --requests 50
docker compose run --rm collector status RUN_ID
docker compose run --rm collector inspect VACANCY_ID
docker compose run --rm collector export RUN_ID --output /data/exports/run.json
```

`--rm` удаляет только завершившийся контейнер. Бюджеты, `partial` и exit code 2
сохраняют прежний смысл: это не авария Docker. Повторный `resume` с тем же run ID
продолжает сохранённый план, а не создаёт новую выборку. Без команды выводится help.
Один активный collector на ledger защищён flock между контейнерами на этом volume.

Default prefix entrypoint:
`--db /data/ledger.sqlite3 --env /run/secrets/career_env --token-file /data/auth/hh-token.json`.
Общие аргументы можно переопределить до подкоманды, например:
`docker compose run --rm collector --db /data/test.sqlite3 status RUN_ID`.

## Где находятся данные и ключи

| Объект | Размещение |
|---|---|
| SQLite, WAL, SHM, flock | `/data/ledger.sqlite3` и соседние файлы, volume `career-scout-data` |
| Экспорты | `/data/exports/`, тот же volume |
| Обновляемый HH token | `/data/auth/hh-token.json`, атомарная запись, 0600 |
| Исходные app credentials | Канонический host `.env`, read-only secret `/run/secrets/career_env` |

`.env` не передаётся через Docker build ARG или environment. Compose монтирует его
только при запуске. Cache содержит токен и hash app ID; токен другого приложения
не подменяет текущий. Плохой cache даёт безопасный `invalid_token_cache`.
При наличии `--token-file` обновление токена не меняет исходный `.env`; без этого
флага прежний host CLI продолжает атомарно обновлять `.env` как раньше.

Процесс внутри контейнера имеет UID 0, чтобы прочитать root-owned host `.env` 0600,
с отключёнными capabilities, no-new-privileges и read-only root filesystem.
Запись разрешена в `/data` и временный `/tmp`. Entry point устанавливает umask 077;
порты не публикуются, Docker socket и другие сервисы в контейнер не подключаются.

На текущем сервере Docker показывает mountpoint
`/var/lib/docker/volumes/career-scout-data/_data`.
Путь управляется Docker; для работы с БД использовать контейнер и `/data`,
а не менять файлы из mountpoint вручную.

## Перенос прежней базы

В volume импортирована согласованная копия проверенной БД с 315 full карточками
через SQLite backup API, с отказом перезаписывать существующий ledger.
Исходник `data/hh-volume-20261111.sqlite3` оставлен без изменений.
Не копировать один `.sqlite3` у активного WAL writer обычным `cp`: последние
commits могут находиться в WAL. Для следующей миграции использовать SQLite backup
и проверять quick_check/foreign_key_check, количество карточек и snapshots.

## Проверки

```sh
# Docker engine нужен; HH/LLM/реальные ключи не нужны
docker build -t career-scout:local .
python3 scripts/check_container.py
```

Тест создаёт собственный уникальный volume с synthetic данными: 300 карточек,
первый контейнер сохраняет 20 full и partial, удаляется; второй читает ту же БД,
cache и run ID, продолжает до 300 full/complete. HTTP полностью MockTransport,
Docker network=none. Проверены отсутствие повторного detail для сохранённых ID,
303 попытки на 3 страницы/300 карточек, export и SQLite integrity. После теста
удаляется только его synthetic volume; рабочий `career-scout-data` не затрагивается.
Проверка включена в отдельный CI job.

Фактическая серверная приёмка: [docker-live-20261111.json](references/docker-live-20261111.json).
Удалён настоящий первый контейнер после budgeted HH search; новый контейнер
продолжил тот же run и ledger, не потеряв исходные 315 snapshots. Четыре реальных
HTTP-запроса (search/detail) суммарно; широкий run намеренно остаётся partial по
бюджету. Это проверка Docker persistence, не повторная нагрузочная приёмка рынка.

Сбор до Docker и настоящий SIGKILL: [hh-volume-acceptance.md](hh-volume-acceptance.md).
