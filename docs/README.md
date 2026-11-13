# Документация

Текущий этап: продолжающийся сбор и разработка анализатора через подписку Codex.

## Задача и решения

- [product.md](product.md) — результат, границы, открытые вопросы.
- [architecture.md](architecture.md) — компоненты и путь вакансии.
- [component-adaptation.md](component-adaptation.md) — перенос по файлам, зависимости и замены.
- [reuse.md](reuse.md) — выбор сборщика и точные донорские исходники.
- [adr/0004-collector-first.md](adr/0004-collector-first.md) — текущий приоритет: самостоятельный коллектор.
- [adr/0001-stack.md](adr/0001-stack.md) — Python, LangGraph, SQLite.
- [adr/0002-routing.md](adr/0002-routing.md) — native Decisions, четыре очереди и legacy first-PASS.
- [adr/0003-source-boundaries.md](adr/0003-source-boundaries.md) — транспорт и права источников.

## Контракты

- [core-contracts.md](core-contracts.md) — границы источников, качество данных,
  проверка доказательств, очередь резюме и переносимые подключения.
- [interface-choice.md](interface-choice.md) — выбор CLI/MCP для личной установки,
  подготовительный блок и план измерения качества.

- [exclusions.md](exclusions.md) — постоянный отсев до очереди, моделей и ассистента.

- [evaluation-performance.md](evaluation-performance.md) — сравнение10/20, токены и консервативный Decisions screen.

- [full-array.md](full-array.md) — конечный проход всего снимка по площадкам, продолжение без повторов.

- [applications-first-20261112.md](applications-first-20261112.md) — первые100карточек,
  14рекомендаций, native-проверки и продвижение следующей порции.
- [applications.md](applications.md) — текущий отбор для отклика без вопросов, следующие
  порции и выдача конкретных рекомендованных вакансий.

- [routing-batches.md](routing-batches.md) — один/два профиля, четыре параллельных батча,
  budgets/resume и фактическая калибровка100.
- [decisions-routing100-20261112.md](decisions-routing100-20261112.md) — протокол и метрики100.

- [multi-source.md](multi-source.md) — четыре адаптера, общий CLI, native pagination и статус SuperJob.

- [normalization.md](normalization.md) — общий формат v2, source identity, условия и миграция.

- [collector-readiness.md](collector-readiness.md) — live-доступ и остаток реализации.
- [collector.md](collector.md) — поиск, страницы, окна, бюджет и полнота.
- [sources/README.md](sources/README.md) — четыре площадки и статус доступа.
- [filtering.md](filtering.md) — рабочий TOML, семейства, допустимые грейды, unknown и история решений.
- [evaluation.md](evaluation.md) — Codex CLI, ручные prepare/run/resume/export,
  вход/выход v2, маршрутизация и определения вердиктов.
- [storage.md](storage.md) — идентичность, версии, очередь и транзакции.
- [security.md](security.md) — секреты, данные, действия и tracing.
- ../schemas/ — проверяемые JSON Schema.
- ../prompts/ — общий контракт и четыре предметных профиля.

## Подготовка и приёмка

- [development.md](development.md) — действующие команды initial/new и legacy collect и аргументы CLI.
- [docker.md](docker.md) — контейнер, persistent volume, credentials и проверка пересоздания.
- [operations.md](operations.md) — запуск, восстановление, лимиты, диагностика.
- [hh-volume-acceptance.md](hh-volume-acceptance.md) — фактическая проверка сотен HH карточек и SIGKILL/resume.
- [acceptance.md](acceptance.md) — сценарии и показатели качества.
- [roadmap.md](roadmap.md) — коллектор, анализатор и незакрытая приёмка.
- [backlog.json](backlog.json) — машиночитаемые зависимости и статусы задач.
- [references/README.md](references/README.md) — официальная документация и XERJ.
- [research/README.md](research/README.md) — результат исследования аналогов.

Слова «должен» описывают целевой контракт. Реализованное перечислено в корневом
README и docs/roadmap.md. Эта документация не доказывает запуск сервиса.
