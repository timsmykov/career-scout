# Диагностика

Команды нового `career-scout` печатают один JSON. У ошибки есть error.code;
результат частичного run смотрите отдельно. Не публикуйте credentials, резюме,
полные вакансии, raw prompts или рабочую SQLite. [Безопасный отчёт](../SECURITY.md).

## Сначала проверьте состояние

```sh
career-scout version
career-scout --workspace private/me run status COLLECTION_RUN_ID
career-scout --workspace private/me evaluation status EVALUATION_RUN_ID --db DATABASE
career-scout --workspace private/me operation show REQUEST_ID
```

Collection ID и evaluation ID различны. DATABASE берётся из evaluation prepare.
Новый CLI и прежний `python -m career_scout` имеют разные ответы и exit codes:
не применяйте инструкции одного интерфейса к другому.

| Код/симптом | Причина | Действие |
|---|---|---|
| invalid_arguments | Не совпали команда или обязательные флаги | Откройте help конкретной команды |
| input_unreadable_or_invalid_json | Путь, JSON или размер входа | Проверьте приватный файл и схему; не вставляйте секреты в Issue |
| request_id_conflict | Этот ID использован с другими аргументами | Для нового действия возьмите новый ID; прежнюю квитанцию сохраните |
| operation_incomplete | Нет итоговой квитанции; действия могли произойти | Сверьте operation/run status до продолжения |
| version_conflict | Контекст изменён конкурентно | Перечитайте context show; обновляйте с текущим if-version |
| plan_context_stale | План привязан к старому контексту | Создайте новый plan для новой версии |
| source_storage_policy_unconfirmed | SuperJob выбран без storage_allowed | Проверьте своё разрешение и явно задайте настройку |
| run_busy / evaluation_busy | Уже есть исполнитель под lock | Проверьте текущий процесс; не запускайте второй пул |
| partial при status=ok | Команда закончила порцию, весь run ещё нет | Смотрите остаток бюджета, report.sources и execution_error |
| model_calls_disabled | Внешнее выполнение не разрешено | После проверки входов/бюджетов явно используйте enable-calls |
| decisions_connection_missing | Нужен router, но нет подключения | Настройте совместимый коннектор; одному CV при выключенном автоотсеве он не нужен |
| codex_unavailable | Исполнитель Codex не запускается | Проверьте установку, executable и права в своей среде |
| claude_unavailable / claude_isolation_flags_unavailable | Claude CLI не запускается или не поддерживает нужные флаги | Проверьте установку и версию; не отключайте изоляцию ради обхода |
| claude_subscription_login_required | Нет действующего входа через claude.ai | Настройте свой подписочный вход; API login — другое подключение |
| model_api_key_missing_or_invalid | Нет ключа выбранного API | Проверьте имя переменной, --env и свой файл; значение ключа не выводите |
| model_authentication_failed / model_access_denied | API отклонил доступ | Проверьте своё подключение и доступность модели |
| model_rate_limited | Провайдер вернул 429 | Проверьте лимиты провайдера; запуск остановлен без скрытого повтора |
| model_request_rejected / model_invalid_response | API отклонил параметры или вернул невалидный ответ | Проверьте model ID, JSON/schema режим и лимиты ответа по model-providers |
| batch_outcome_unknown | Неизвестно, завершился ли внешний вызов | Сверьте сохранённую попытку и процесс; не сбрасывайте журнал ради повтора |
| model_outcome_unknown / native-код с outcome_unknown | Запрос мог выполниться до timeout/разрыва | Сверьте сохранённую попытку и историю провайдера; новый request-id не разрешает повтор |
| input_exceeds_decisions_context | Вход больше разрешённого объёма | Уточните контекст/лимит осознанно; программа не обрезает его молча |
| cursor_stale | Список изменился после получения cursor | Начните чтение списка без cursor |
| labels_context_mismatch | Метки относятся к другой версии | Используйте разметку с соответствующим context_hash |
| doctor ready=false | Не готов один из нужных компонентов | Передайте --context CONTEXT_ID и смотрите next/checks; без context необходимость Decisions не проверена |
| Пустой shortlist | Нет PASS, истекло окно, сработала история или есть ошибки | Смотрите status, include-uncertain и explain |

## Внешняя площадка не отвечает

401/403, captcha, 429 и parser drift должны оставаться наблюдаемой причиной,
а не превращаться в «вакансий нет». Не обходите доступ сменой аккаунта, IP или
транспорта. Для cooldown дождитесь retry_at; для auth проверьте собственное
подключение. Habr может потребовать адаптации после изменения frontend.

[Продолжение и перенос](operations.md) · [Сбор](guides/collection.md) ·
[Анализ](guides/evaluation.md) · [JSON-контракт](agent-cli.md).
