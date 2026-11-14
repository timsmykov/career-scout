# Проверки

Тесты находятся непосредственно в tests/test_*.py; инструкции ниже не требуют
несуществующих каталогов unit/collectors/evaluation.

```sh
uv run --no-sync pytest -m "not live"
```

| Область | Файлы |
|---|---|
| CLI, квитанции, версии и budgets | test_agent_cli.py |
| Персональный context-1 | test_context_engine.py |
| Десять API, Claude, установка навыка и unknown delivery | test_model_providers.py |
| Источники, миграции и нормализация | test_hh_collector.py, test_migration.py, test_normalization.py |
| Ранний анализ, routed queues | test_analysis.py, test_routed_analysis.py |
| Технические контракты | test_core_contracts.py |

Это навигация, не полный список: дополнительные test_*.py покрывают кампании,
выдачу, исключения, performance и другие сценарии. Используются временные SQLite,
контролируемый HTTP и fake model executable. Сети, реальных ключей и модельных
вызовов в обычном CI нет.

После Docker-сборки scripts/check_container.py проверяет перенос/пересоздание и
сохранение данных; scripts/check_product.py — весь поток установленного пакета
с эмуляторами. check_repository.py валидирует схемы/примеры и локальные ссылки.

Живые проверки авторизации, API, Codex и Claude — отдельные конечные сценарии. Корректная
схема и цитата не доказывают смысловую точность. [Аудит0.1](../docs/product-audit-0.1.md),
[критерии](../docs/acceptance.md), [CONTRIBUTING](../CONTRIBUTING.md).
