# Документация

Текущий этап: коллектор данных. Оценка/LangGraph/профили — отложенные материалы.

## Задача и решения

- [product.md](product.md) — результат, границы, открытые вопросы.
- [architecture.md](architecture.md) — компоненты и путь вакансии.
- [component-adaptation.md](component-adaptation.md) — перенос по файлам, зависимости и замены.
- [reuse.md](reuse.md) — выбор сборщика и точные донорские исходники.
- [adr/0004-collector-first.md](adr/0004-collector-first.md) — текущий приоритет: самостоятельный коллектор.
- [adr/0001-stack.md](adr/0001-stack.md) — Python, LangGraph, SQLite.
- [adr/0002-routing.md](adr/0002-routing.md) — последовательность и первый PASS.
- [adr/0003-source-boundaries.md](adr/0003-source-boundaries.md) — транспорт и права источников.

## Контракты

- [normalization.md](normalization.md) — общий формат v2, source identity, условия и миграция.

- [collector-readiness.md](collector-readiness.md) — live-доступ и остаток реализации.
- [collector.md](collector.md) — поиск, страницы, окна, бюджет и полнота.
- [sources/README.md](sources/README.md) — четыре площадки и статус доступа.
- [filtering.md](filtering.md) — допустимые грейды и работа с неизвестным.
- [evaluation.md](evaluation.md) — вход/выход, маршрутизация, определения вердиктов.
- [storage.md](storage.md) — идентичность, версии, очередь и транзакции.
- [security.md](security.md) — секреты, данные, действия и tracing.
- ../schemas/ — проверяемые JSON Schema.
- ../prompts/ — общий контракт и четыре предметных профиля.

## Подготовка и приёмка

- [development.md](development.md) — действующие HH-команды и аргументы CLI.
- [docker.md](docker.md) — контейнер, persistent volume, credentials и проверка пересоздания.
- [operations.md](operations.md) — запуск, восстановление, лимиты, диагностика.
- [hh-volume-acceptance.md](hh-volume-acceptance.md) — фактическая проверка сотен HH карточек и SIGKILL/resume.
- [acceptance.md](acceptance.md) — сценарии и показатели качества.
- [roadmap.md](roadmap.md) — фичи коллектора, 28 активных и 9 отложенных задач.
- [backlog.json](backlog.json) — машиночитаемые зависимости и статусы задач.
- [references/README.md](references/README.md) — официальная документация и XERJ.
- [research/README.md](research/README.md) — результат исследования аналогов.

Слова «должен» описывают целевой контракт. Реализованное перечислено в корневом
README и docs/roadmap.md. Эта документация не доказывает запуск сервиса.
