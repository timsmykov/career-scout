# Документация Career Scout

**0.1.0 alpha · MIT · российский рынок.** Публичная реализация — `main`.
Международный поиск пока не поддерживается: для него нужен отдельный механизм.
Основной интерфейс — `career-scout`, личный JSON CLI. Документы о ранних четырёх
профилях и рабочих запусках собраны отдельно от инструкций для нового пользователя.

- [agent-skill.md](agent-skill.md) — установка навыка для Codex/Claude и работа агента.
- [model-providers.md](model-providers.md) — десять API, две подписки и границы проверки.

## Начать с результата

| Мне нужно | Читать |
|---|---|
| Понять, что это и подходит ли мне | [Задача и границы](product.md), [аудит 0.1](product-audit-0.1.md) |
| Получить первую подборку | [Первый запуск с одним резюме](quickstart.md) |
| Настроить резюме и условия | [Контекст кандидата](guides/candidate.md) |
| Собрать и затем добавлять новые вакансии | [Поиск и сбор](guides/collection.md) |
| Выполнить анализ и прочитать результат | [Анализ и подборка](guides/evaluation.md) |
| Продолжить после остановки | [Эксплуатация](operations.md), [диагностика](troubleshooting.md) |
| Внести изменение | [CONTRIBUTING](../CONTRIBUTING.md), [исходники](../src/career_scout/README.md) |

- [personalization.md](personalization.md) — настройка своей установки и граница с приватным deployment.
- [public-release.md](public-release.md) — обезличивание и проверка публичной поставки.

## Руководства и объяснения

- [quickstart.md](quickstart.md) — установка, минимальные входы, ограниченный сбор и первая оценка.
- [guides/candidate.md](guides/candidate.md) — резюме, приоритеты, fact_rules, версии.
- [guides/collection.md](guides/collection.md) — четыре источника, планы, initial/new/resume и бюджеты.
- [guides/evaluation.md](guides/evaluation.md) — один/несколько CV, подключение, статусы, история и метрики.
- [operations.md](operations.md) — личные каталоги, восстановление и перенос данных.
- [troubleshooting.md](troubleshooting.md) — коды ошибок и конкретные действия.
- [docker.md](docker.md) — контейнер коллектора, persistent volume и пересоздание.
- [architecture.md](architecture.md) — актуальная схема, компоненты и ответственность кода/моделей.
- [product.md](product.md) — результат, поддерживаемый сценарий и границы 0.1.
- [case-study.md](case-study.md) — задача, инженерные решения, наблюдаемый результат и ограничения.
- [interface-choice.md](interface-choice.md) — почему личный CLI, а не обязательный общий сервер.

## Справочник и контракты

- [agent-cli.md](agent-cli.md) — команды, JSON-ответы, request-id, версии и пагинация выдачи.
- [context-evaluation.md](context-evaluation.md) — context-1, Decisions/Codex, доказательства и допустимые выводы.
- [core-contracts.md](core-contracts.md) — данные, очередь и переносимые исполнители.
- [normalization.md](normalization.md), [storage.md](storage.md) — vacancy v2, SQLite v5, исходники и миграции.
- [collector.md](collector.md), [multi-source.md](multi-source.md) — страницы, полнота и адаптеры.
- [sources/README.md](sources/README.md) — отдельные контракты HH/Habr/SuperJob/Trudvsem.
- [security.md](security.md), [SECURITY](../SECURITY.md) — данные, подключения и безопасные отчёты.
- [Схемы](../schemas/agent-context.schema.json), [примеры](../examples/README.md) — проверяемые входы; `career-scout schema` возвращает полный текущий контракт.
- [reuse.md](reuse.md), [component-adaptation.md](component-adaptation.md), [NOTICE](../NOTICE.md) — референсы, происхождение и права.

## Проверки, развитие и история

- [cli-audit-20261114.md](cli-audit-20261114.md) — текущий аудит CLI, навыка и модельных подключений.

- [product-audit-0.1.md](product-audit-0.1.md) — продуктовая приёмка; где тест, эмулятор, live и историческое свидетельство.
- [documentation-audit.md](documentation-audit.md) — покрытие документацией, проверка примеров и оставшийся долг.
- [acceptance.md](acceptance.md), [тесты](../tests/README.md) — критерии и воспроизводимые проверки.
- [CHANGELOG](../CHANGELOG.md) — изменения текущей alpha; опубликованный релиз не подразумевается.
- [roadmap.md](roadmap.md), [backlog.json](backlog.json) — датированный план и критерии; старые статусы не заменяют текущий аудит.
- [references/README.md](references/README.md) — источники API-документации и XERJ.
- [research/README.md](research/README.md) — исследование аналогов.
- [context-acceptance-20261113.json](context-acceptance-20261113.json) — ограниченная живая модельная приёмка на вымышленных данных.

## Прежние режимы и датированные результаты

Эти материалы сохраняют воспроизводимость старых запусков. Их четыре профессии,
фильтры и результаты не становятся настройками нового пользователя автоматически.

- [development.md](development.md) — прежний CLI `python -m career_scout`, initial/new и legacy collect.
- [filtering.md](filtering.md) — TOML и прежние IT/grade/experience-фильтры.
- [evaluation.md](evaluation.md) — старые prepare/run/resume/export и результат v2.
- [applications.md](applications.md) — applications-1, следующий остаток, результат v3.
- [routing-batches.md](routing-batches.md) — native routing, один/два профиля и очереди.
- [evaluation-performance.md](evaluation-performance.md) — сравнение батчей 10/20 и Decisions screen.
- [exclusions.md](exclusions.md) — постоянные исключения старого режима.
- [full-array.md](full-array.md) — фиксированный проход по площадкам и восстановление.
- [openbot-assistant.md](openbot-assistant.md) — прежний доступ ассистента по запросу.
- [collector-readiness.md](collector-readiness.md), [hh-volume-acceptance.md](hh-volume-acceptance.md) — ранние probe и объёмные проверки.
- [applications-first-20261112.md](applications-first-20261112.md), [quality-first-20261112.md](quality-first-20261112.md) — первые пользовательские оценки.
- [decisions-routing100-20261112.md](decisions-routing100-20261112.md) — разметка и калибровка конкретного router.

История решений: [0001: стек](adr/0001-stack.md), [0002: маршрутизация](adr/0002-routing.md),
[0003: источники](adr/0003-source-boundaries.md), [0004: collector first](adr/0004-collector-first.md).
Ранний collector-first описывает порядок разработки, а не запрет уже реализованного анализа.
