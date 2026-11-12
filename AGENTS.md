# Работа с Career Scout

## Scope

Это новый самостоятельный сервис, а не изменение существующего OpenBot Scout.
Перед изменением читать README.md и docs/README.md, затем нужный предметный контракт.
Реализованы HH и adapters.py (Trudvsem/Habr/SuperJob), общий transport.py,
source_normalization.py, source_types.py, storage/collector/CLI.
Точный статус: docs/multi-source.md. Владелец 11.11.2026 подтвердил наличие всех
разрешений на хранение вакансий. SuperJob storage_allowed=true в рабочем TOML;
постоянный сбор разрешён для всех четырёх источников. Probe остаётся режимом без записи.
Docker/Compose: batch CLI, external volume career-scout-data; .env — read-only secret,
HH token cache — в volume. Не удалять этот volume и не добавлять scheduler без запроса.
SQLite schema v5 (vacancy/export v2): source+source_id, immutable snapshots и отдельные common-1
normalized_snapshots. V1/v2/v3/v4 авто-мигрируются с backup под flock. Не переписывать
старый payload/content_hash при изменении нормализатора. Контракт: docs/normalization.md.
Нативный page size сохраняется в query_pagination; HH cap2000, SJ500,
Trudvsem offset=page0, Habr page1. Не переносить HH geography/Boolean syntax в другие API.
Рабочий config: configs/search.toml; frozen plan/resume, offline refilter и история
решений в docs/filtering.md. Unknown/conflict сохранять; зарплата не фильтрует.
12.11.2026 владелец разрешил разработку анализа параллельно продолжающемуся сбору.
Анализатор читает только готовые full записи через read-only соединение и хранит
собственные inputs/results/attempts/checkpoints в отдельной SQLite.
Контракт и ручной запуск: docs/evaluation.md; entrypoint: career_scout.analysis.

Основной путь — campaigns.py: initial/new/resume CAMPAIGN_ID. Append-only: все
видимые ID, full descriptions только неизвестных, без TTL refresh. Initial и manual new —
14дней по published_at, frozen start/end; modified_at не доказывает свежесть.
Old/date-unknown хранить отдельно от свежей выгрузки, прежние записи не удалять. Конечный HTTP
budget ограничивает chunk, не общий объём источника. Daily manual, без расписаний.
Legacy collect сохраняется для прежних run IDs и имеет отдельный TTL-контракт.

## Инварианты

- Сбор, HTTP, пагинация, дедупликация, лимиты и очередь — код, без LLM.
- Воркер получает один полный актуальный текст резюме, вакансию и атрибуты владельца.
  Общая память не добавляет кандидату навыков.
- Подтверждённый порядок: product → automation → business_assistant → business_analyst.
  Универсальное и исследовательское резюме не подключать.
- Внутри одной вакансии оценка последовательна, первый PASS завершает.
  BORDERLINE и ERROR продолжают маршрут. Общий отказ — только четыре валидных REJECT.
- Текст карточки неполный или несогласованно устаревший — дочитать, не отвергать.
- У источника 401/403/429 — отразить состояние. Не менять IP/аккаунт/транспорт для обхода.
- Не отправлять отклики, не править резюме и не создавать расписания в рамках MVP.
- Не переносить credentials из OpenBot/подписочных сессий.
- Вызовы модели — native Codex CLI через существующий ChatGPT login, не model API.
  Подтверждены gpt-6-luna/max и порядок выше. Не копировать auth.json в приложение.
  Worker запускается с пустым рабочим каталогом, без MCP/apps/tools/памяти; один
  WorkerInput содержит только одно резюме. Персональные исходники не коммитить.
- Новые зависимости добавлять только по реализации этапа. Проверять лицензии до копирования.

## Структура и проверки

Python 3.12, uv.lock, src-layout, httpx/Pydantic, SQLite.
LangGraph управляет маршрутом; codex_worker.py вызывает установленный host Codex CLI.
Offline-тесты используют подставной executable, без подписки и model calls.
Обязательные offline-проверки:
`uv run --no-sync python scripts/check_repository.py`,
`uv run --no-sync ruff check .`, `uv run --no-sync ruff format --check .`,
`uv run --no-sync pytest -m "not live"`.

Docker: `docker compose build`, `python3 scripts/check_container.py` (synthetic, без сети).
Для поведения писать тесты по docs/acceptance.md; сетевые проверки явно отделять.
Не запускать реальный сбор/модели для проверки изменения Markdown.
Обычный CI не получает секретов и не ходит к job API/LLM.
Документацию обновлять в том же изменении, что меняет контракт.
