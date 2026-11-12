# Модули

Коллектор запускается через `python -m career_scout`; анализатор — отдельно через
`python -m career_scout.analysis`. Модельные вызовы не входят в путь сбора.

| Модули | Назначение |
|---|---|
| hh.py, adapters.py, transport.py, source_types.py | Четыре источника, HTTP/auth, native pagination и ошибки |
| collector.py, campaigns.py | Legacy collect и append-only initial/new/resume |
| storage.py, migrations.py | Collector SQLite schema5, миграции с backup, snapshots и discoveries |
| models.py, normalization.py, source_normalization.py | Общая вакансия v2 и нормализация источников |
| filtering.py, search_config.py | Поисковый план и детерминированные фильтры |
| cli.py, __main__.py | Команды коллектора |
| evaluation.py | Версии полного входа, результата и проверка цитат |
| evidence.py | Полные исходные тексты с адресами фрагментов, восстановление непрерывных цитат |
| codex_worker.py | Изолированный native Codex CLI через ChatGPT login |
| analysis_store.py, analysis_graph.py | Отдельная приватная SQLite, кэш, бюджет, попытки и LangGraph checkpoints |
| analysis.py | Ручные prepare/run/resume/status/export, read-only доступ к collector ledger |

Точные команды и ограничения: [evaluation.md](../../docs/evaluation.md).
