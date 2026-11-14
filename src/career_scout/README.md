# Карта модулей

Основной интерфейс — установленный `career-scout` → agent_cli. Коллектор и
модельный анализ разделены; данные старых запусков сохраняют свои контракты.

| Модули | Назначение |
|---|---|
| agent_cli, agent_models | Команды JSON CLI и схема контекста/плана/лимитов |
| agent_service, agent_results, agent_state | Общие операции, версии, request-id, контрольная SQLite, пагинация |
| context_models, context_commands | Подключения, команды оценки, история, doctor и метрики |
| context_engine, context_policy | Admission, fact_rules, маршруты произвольных CV, оценка и допустимость результата |
| hh, adapters, transport, sources, source_types | Четыре источника, HTTP/auth, native pagination и ошибки |
| campaigns, collector | Append-only initial/new/resume и legacy collect |
| storage, migrations | Collector schema v5, backup/migration, snapshots и discoveries |
| models, normalization, source_normalization, data_quality | Общая vacancy v2, поля, unknown/conflict, качество данных |
| filtering, search_config | Детерминированный план и фильтры источников |
| decisions, prescreen, fit_screen | Native Decisions-клиент и прежние этапы отбора |
| codex_worker, batch_worker | Изолированный Codex CLI, одно CV на батч, модельная transport schema |
| evaluation, application, evidence | Форматы v2/v3/v4, полные исходники, цитаты и проверки |
| analysis, analysis_store | Прежний CLI анализа и общая SQLite оценок/попыток/бюджетов |
| routed_pipeline, analysis_graph, array_analysis | Профильные очереди, legacy first-PASS/LangGraph, конечный проход массива |
| shortlist, exclusions, assistant_access, intake | Выдача, прежние исключения, история и доступ ассистента |
| cli, __main__ | Прежний интерфейс коллектора `python -m career_scout` |
| common, html | Общие утилиты и извлечение текста |

[Архитектура](../../docs/architecture.md) · [CLI](../../docs/agent-cli.md) ·
[Персональная оценка](../../docs/context-evaluation.md) · [Legacy-анализ](../../docs/evaluation.md).
