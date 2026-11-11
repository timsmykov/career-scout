# Тесты

`test_hh_collector.py` проверяет HH transport, нормализацию, транзакции, версии,
пагинацию, date split, budgets/429/auth, CLI и аварийный выход отдельного процесса.
Запуск: `uv run --no-sync pytest -m "not live"`. Сети, реальных токенов и LLM нет.
Ограниченная реальная проверка HH фиксируется отдельно в docs/collector-readiness.md.
Общий оставшийся план: docs/acceptance.md.

Добавлять тесты в одном изменении с реализацией:
unit/ — фильтры, версии, агрегирование;
collectors/ — HTTP через respx и вымышленные фикстуры;
storage/ — реальные временные SQLite, транзакции и leases;
evaluation/ — fake model adapter, порядок вызовов, ошибки, цитаты;
live/ — только отдельный явный запуск.

Текущие JSON/TOML/Markdown-контракты проверяет scripts/check_repository.py.
Не проверять бизнес-логику тестом, который только повторяет её исходник.
