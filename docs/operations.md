# Эксплуатационный контракт

Runtime ещё не реализован. Действующие команды подготовки — docs/development.md.

## До первого запроса

Проверить source enabled, app identity/contact, тип токена, разрешённые методы,
capabilities, хранение/передачу текста, конечный бюджет. Secret values не печатать.
Перед оценкой дополнительно: четыре актуальных резюме, подтверждённый порядок,
рубрики, версия фильтра, действующий API модели и лимит расходов.

configs/project.example.toml выключает внешние вызовы. Это шаблон, не готовое
разрешение включить все источники. Ранее в проекте проверены HH/Trudvsem/Habr,
но новое окружение должно явно прочитать свои разрешённые credentials.

## Рекомендуемый пилот

1. Offline fixtures: pages, full detail, повтор, stale, schema drift, rate limit.
2. Один официальный источник HH: ограниченный read-only поиск/detail.
3. Сохранение/перезапуск без повторной потери данных.
4. Четыре свежих резюме и небольшой размеченный набор.
5. Только затем дополнительные источники и параллельные вакансии.

## Обязательный итог run

Для каждого источника: статус, planned/completed queries, страницы, лимиты,
distinct discoveries, full/deferred cards, errors. Для evaluation:
accepted/rejected/clarification/pending, skipped profiles, attempts, usage.
Отдельно необработанный хвост и причины. Нулевой результат при blocked source
нельзя представлять как отсутствие рынка.

## Сценарии сбоев

| Событие | Действие |
|---|---|
| Истёк app token HH | Один поддерживаемый refresh/acquire; при отказе auth_required |
| OAuth требует участия пользователя | Остановить источник и показать причину |
| 403/captcha | Не обходить; доступ требует решения владельца |
| 429 / quota-like SuperJob 503 | Cooldown, Retry-After/консервативная задержка, общий бюджет |
| Изменилась схема | Изолировать rejected payload, parser_drift; не оценивать пустые поля |
| Не загрузился detail | Оставить дочитывание с bounded retry |
| Источник недоступен | Другие продолжают; run partial |
| LLM timeout/плохой JSON | Attempt error; ограниченный повтор; затем следующий профиль |
| Процесс упал | Lease recovery + ledger lookup + checkpoint reconcile |
| Нет средств/достигнут budget | Pending, не отрицательная оценка |

## Ограничения

Default min interval 1s, max in-flight 1 на источник — наша осторожная политика.
Это не обещание официального лимита и не доказательство достаточности.
В bootstrap нет cron, systemd, Docker deployment, внешнего бота или нового OpenBot flow.
