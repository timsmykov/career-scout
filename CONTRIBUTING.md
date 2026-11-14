# Участие в разработке

Career Scout 0.1 — личный CLI для сбора и оценки вакансий. Начните с
[архитектуры](https://github.com/timsmykov/career-scout/blob/main/docs/architecture.md), [контракта CLI](https://github.com/timsmykov/career-scout/blob/main/docs/agent-cli.md) и
[границ alpha](https://github.com/timsmykov/career-scout/blob/main/docs/product-audit-0.1.md). Правила работы агента — в [AGENTS.md](https://github.com/timsmykov/career-scout/blob/main/AGENTS.md).

## Локальная среда

Нужны Linux, Python 3.12 и uv. Публичная ветка main содержит runtime. Для своего изменения создайте feature-ветку.

```sh
uv sync --locked --group dev
uv run --no-sync career-scout version
uv run --no-sync career-scout schema
```

Для разработки и offline-тестов не требуются реальные ключи, резюме и Codex.
Обычный CI не имеет доступа к job API и моделям.

## Проверки перед PR

```sh
uv run --no-sync python scripts/check_repository.py
uv run --no-sync python scripts/check_public_distribution.py
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -m "not live"
uv build
```

При изменении упаковки, хранения или основного пользовательского потока:

```sh
docker compose build
python3 scripts/check_container.py
python3 scripts/check_product.py
```

Первый сценарий проверяет данные после пересоздания контейнера, второй — путь
установленного пакета от сбора до подборки с эмуляторами HTTP/Codex.
Это не проверки живой авторизации или смысловой точности. [Карта тестов](https://github.com/timsmykov/career-scout/blob/main/tests/README.md).

## Где менять код

| Изменение | Основные модули |
|---|---|
| CLI, команды и установка навыка | agent_cli, agent_service, agent_results, agent_state, agent_skill |
| Контекст и персональный анализ | agent_models, context_models, context_commands, context_engine |
| Источник/страницы/транспорт | hh, adapters, source_types, transport, campaigns |
| Хранение и общий формат | storage, migrations, models, normalization, source_normalization |
| Исполнители и доказательства | model_providers, codex_worker, claude_worker, batch_worker, evidence |

[Полная карта](https://github.com/timsmykov/career-scout/blob/main/src/career_scout/README.md). Legacy-анализ и старые данные сохраняют
свои контракты; не меняйте их под видом нового персонального режима.

## Как подготовить изменение

1. Опишите наблюдаемую проблему и ожидаемое поведение.
2. Ограничьте diff одной задачей; добавьте тест на значимое изменение поведения.
3. Используйте вымышленные фикстуры, временные SQLite и fake executable модели.
4. При изменении формата данных добавьте явную версию/миграцию и проверку сохранности.
5. Обновите соответствующие команды, схемы, примеры и документацию в том же PR.
6. Укажите выполненные проверки и оставшиеся ограничения.

[Шаблон PR](.github/pull_request_template.md) содержит поля для результата и проверок.

Сетевые проверки запускаются отдельно с явным конечным бюджетом. Никогда не
включайте реальные модельные вызовы в обычный тест. Не меняйте автоматически
авторизацию, провайдеров и настройки сервисов разработчика.

## Сообщения об ошибках

Используйте шаблон Issue: версия, команда с заменёнными личными значениями,
ожидание, безопасный код ошибки и воспроизведение. Не прикладывайте рабочую базу,
полное резюме, вакансии, credentials, cookies или model prompts.
[Правила безопасного отчёта](https://github.com/timsmykov/career-scout/blob/main/SECURITY.md).

Собственный код лицензирован [MIT](https://github.com/timsmykov/career-scout/blob/main/LICENSE). При переносе стороннего кода
сохраняйте его лицензию, copyright и происхождение по [NOTICE](https://github.com/timsmykov/career-scout/blob/main/NOTICE.md).
