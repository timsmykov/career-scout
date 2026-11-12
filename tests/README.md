# Тесты

`test_hh_collector.py` проверяет HH transport, нормализацию, транзакции, версии,
пагинацию, date split, budgets/429/auth, CLI и аварийный выход отдельного процесса.
Запуск: `uv run --no-sync pytest -m "not live"`. Сети, реальных токенов и LLM нет.
test_normalization.py проверяет typed условия, null/conflicts/provenance и identity;
test_migration.py — v1 backup/rollback/process death/idempotence, пересечение ID и
продолжение старого run. Docker test дополнительно переносит legacy запись.
Ограниченная реальная проверка HH фиксируется отдельно в docs/collector-readiness.md.
Общий оставшийся план: docs/acceptance.md.

`test_analysis.py` проверяет четыре позиции первого PASS, остальные исходы,
кэш/порядок/версии, восстановление после записи результата до checkpoint,
постоянный бюджет, изоляцию SQLite и подставной Codex executable. Проверки CLI
transport включают ChatGPT login, запрет API login/tools, usage, timeout собственной
процессной группы, Ctrl+C родителя и сохранение ошибочного ответа. Source ranges проверяются на
полное сохранение текста, источник, порядок и длину восстанавливаемой цитаты.
Реальные модельные пилоты хранятся приватно в data/; offline suite их не запускает.
Корректный JSON и дословная цитата не доказывают семантическое качество вердикта.

Добавлять тесты в одном изменении с реализацией:
unit/ — фильтры, версии, агрегирование;
collectors/ — HTTP через respx и вымышленные фикстуры;
storage/ — реальные временные SQLite, транзакции и leases;
evaluation/ — fake model adapter, порядок вызовов, ошибки, цитаты;
live/ — только отдельный явный запуск.

Текущие JSON/TOML/Markdown-контракты проверяет scripts/check_repository.py.
Не проверять бизнес-логику тестом, который только повторяет её исходник.
