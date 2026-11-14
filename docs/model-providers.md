# Модели и подписки

Career Scout выбирает транспорт через `connection.model.provider`. Коллектор и
Decisions от этого не меняются. Оценщики сохраняют общий формат входа, батчи до20,
до4слотов, проверку доказательств и конечный бюджет `--max-calls`.

```sh
career-scout provider list
career-scout schema --name connection
```

## Десять API-подключений

Это практический набор поддерживаемых сервисов, не рейтинг популярности. Model ID
задаётся явно: доступность модели, JSON-режим и лимит ответа зависят от аккаунта.

| provider | Переменная по умолчанию | Протокол и первичная документация |
|---|---|---|
| openai | OPENAI_API_KEY | [Responses](https://developers.openai.com/api/reference/resources/responses/methods/create) |
| anthropic | ANTHROPIC_API_KEY | [Messages](https://platform.claude.com/docs/en/api/messages) |
| gemini | GEMINI_API_KEY | [generateContent](https://ai.google.dev/api/generate-content) |
| openrouter | OPENROUTER_API_KEY | [Chat Completions](https://openrouter.ai/docs/api-reference/overview) |
| deepseek | DEEPSEEK_API_KEY | [JSON output](https://api-docs.deepseek.com/guides/json_mode) |
| mistral | MISTRAL_API_KEY | [JSON mode](https://docs.mistral.ai/capabilities/structured_output/json_mode) |
| xai | XAI_API_KEY | [API](https://docs.x.ai/docs/api-reference) |
| groq | GROQ_API_KEY | [OpenAI compatibility](https://console.groq.com/docs/openai) |
| together | TOGETHER_API_KEY | [Compatibility](https://docs.together.ai/docs/openai-api-compatibility) |
| fireworks | FIREWORKS_API_KEY | [Compatibility](https://docs.fireworks.ai/tools-sdks/openai-compatibility) |

Минимальное подключение, модель замените на доступную вам:

```json
{
  "model": {
    "provider": "openrouter",
    "model": "PROVIDER/MODEL",
    "api": {"max_output_tokens": 16000, "output_mode": "json"}
  },
  "external_calls_enabled": false,
  "batch_size": 10,
  "workers": 4
}
```

Ключ задаётся только в окружении процесса или личном env-файле. `api.key_env`
позволяет переименовать переменную, не передавать значение ключа. При наличии
`--env` непустое значение из файла имеет приоритет над окружением. Без флага
evaluation использует `.env` workspace. Файл не выполняется как shell-код.
Содержимое не записывается в connection, SQLite, входы моделей или квитанции.

```sh
career-scout --workspace private/me connection put default --input private/connection.json --request-id api-connection-1
career-scout --workspace private/me doctor --context CONTEXT_ID --env /private/models.env
career-scout --workspace private/me evaluation run RUN_ID --db DATABASE --env /private/models.env --enable-calls --request-id api-run-1
```

`doctor` проверяет локальную настройку, а не доступность API/баланс. Перед большим
проходом выполните свою ограниченную пробу. Ключи API не являются подпиской ChatGPT
или Claude; оплата и лимиты определяются каждым провайдером.

### JSON, reasoning и ошибки

По умолчанию используется JSON mode, Anthropic получает требование JSON в промпте.
В обоих случаях схема и цитаты проверяются локально. `api.output_mode=schema`
дополнительно передаёт JSON Schema OpenAI/Gemini/совместимым API; модель должна
поддерживать эту схему. Для Anthropic этот вариант пока запрещён до запроса.
Нет автоматического ослабления схемы или переключения провайдера при ошибке.

`model.reasoning_effort` относится к CLI-подпискам. Для API отдельный необязательный
`api.reasoning_effort=low|medium|high` поддерживается здесь только у OpenAI и
OpenRouter; выбирайте модель, которая принимает его. Другие параметры не
переносятся между несовместимыми API автоматически.

Каждая попытка имеет ограничение времени и выходных токенов. HTTP-клиент не делает
скрытых повторов; очередь может повторить завершённый некорректный ответ в рамках
своего бюджета. 401/403/429 останавливают запуск с явным кодом. Timeout/разрыв/5xx
помечается `model_outcome_unknown`: запрос мог уже выполниться, поэтому resume
не отправляет его повторно. Проверяйте историю провайдера до ручного восстановления.
Редиректы не исполняются, ключи идут только на фиксированные официальные origins.
API-ответы ограничены16MB; error body не выводится. Измеренные tokens сохраняются,
`cost_usd` остаётся null: автоматического актуального прайс-листа нет.

## Claude через подписку

Используется официальный установленный `claude` CLI с существующим входом
`claude auth login` через claude.ai. Career Scout не извлекает OAuth-токены и не
передаёт подписочную авторизацию в собственные HTTP-запросы.

```json
{"model":{"provider":"claude_subscription","model":"sonnet","reasoning_effort":"high"},"external_calls_enabled":false,"batch_size":10,"workers":4}
```

Нужен современный Claude Code с `--safe-mode`, `--tools`, `--strict-mcp-config`,
`--setting-sources`, `--settings` и `--no-session-persistence`. Установленный CLI
проверяется до модельного запроса. `auth status` должен вернуть loggedIn=true и
authMethod=claude.ai; API login не принимается как подписка. Используется пустой
временный cwd, выключены customizations, hooks, MCP, skills, Chrome и tools;
API/provider override переменные не наследуются. Не используется `--bare`: его
текущая локальная справка отключает OAuth/keychain. Фактические tool_use в ответе
запрещены. Неизвестный результат подписочного вызова также блокирует повтор.

Доступ и учёт headless-вызовов зависят от действующих условий плана Anthropic;
это не обещание неограниченного бесплатного запуска. [Вход](https://code.claude.com/docs/en/authentication),
[CLI](https://code.claude.com/docs/en/cli-reference).

## Codex и Decisions

`codex_subscription` остаётся значением по умолчанию, прежние model hashes
сохранены. Нужен собственный действующий вход Codex через ChatGPT. Native login
не переносится в пакет/образ, настройки других сессий не меняются.

Decisions — отдельное подключение маршрутизатора. При одном CV и выключенном
автоотсеве не нужен. При нескольких CV по-прежнему нужен внешний совместимый
коннектор; десять API-оценщиков не заменяют его. [Контракт](context-evaluation.md).

## Что проверено

Контракты всех десяти транспортов, доказательства, ошибки, отсутствие скрытых
повторов, неизвестная доставка, CLI-discovery и Claude CLI проверяются offline
эмуляторами. Это не подтверждение живого доступа всех аккаунтов/моделей. Платные
вызовы и использование подписок в этой проверке не выполняются. Поддержка Linux /
Python3.12 сохраняется.
