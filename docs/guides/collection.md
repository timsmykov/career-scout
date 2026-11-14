# Поиск, первичный сбор и новые вакансии

Понадобятся сохранённый [контекст](candidate.md), личный workspace и собственный
env-файл. Агент или человек составляет запросы; коллектор исполняет их кодом.

## План на четыре источника

Сохраните как `private/plan.json`. Термины ниже — вымышленный пример, а не
профессиональный фильтр по умолчанию:

```json
{
  "sources": ["hh", "habr", "superjob", "trudvsem"],
  "queries": ["инженер интеграций", "специалист автоматизации"],
  "days": 14,
  "source_options": {"superjob": {"storage_allowed": true}}
}
```

storage_allowed подтверждайте только при своём праве хранения. Для проверки одного
источника оставьте его в sources и уберите неиспользуемые source_options. География:
HH — area_ids; SuperJob — town_id; Habr — city_id; Trudvsem — region_code в параметрах
соответствующего источника. Не переносите идентификаторы между API.

```sh
career-scout --workspace private/me plan put --context CONTEXT_ID   --input private/plan.json --request-id search-plan-1
career-scout --workspace private/me plan preview PLAN_ID
```

Preview работает без HTTP. Новый CLI не подхватывает старый configs/search.toml:
без явной collection_policy нет скрытого ограничения на IT, отрасль или стаж.
[Полный контракт плана](../agent-cli.md).

## Бюджет и первый проход

Пример `private/limits.json`:

```json
{"max_requests": 200, "chunk_requests": 50, "max_rounds": 1}
```

max_requests ограничивает общий объём HTTP-попыток, включая retries, всех площадок
этого run. Это не число вакансий: поиск и detail расходуют отдельные попытки.
chunk_requests ограничивает порцию источника, max_rounds — число обходов за команду.

```sh
career-scout --workspace private/me run start --context CONTEXT_ID --plan PLAN_ID   --limits private/limits.json --env /absolute/path/jobs.env --request-id collection-start-1
career-scout --workspace private/me run execute COLLECTION_RUN_ID --request-id collection-execute-1
career-scout --workspace private/me run status COLLECTION_RUN_ID
career-scout --workspace private/me run results COLLECTION_RUN_ID --limit 20
```

Сохраняйте ID из JSON предыдущей команды. Результат команды status=ok означает
успех конкретной ограниченной операции, не обязательно завершение всей кампании.
Проверяйте result.run_status, report.sources, ошибки и остаток бюджета.
Полные сохранённые карточки доступны через `run results ... --full`.

## Продолжение и следующий день

```sh
career-scout --workspace private/me run resume COLLECTION_RUN_ID   --additional-requests 200 --request-id collection-resume-1
```

Resume продолжает тот же план и фиксированное окно. Дополнительный бюджет
задавайте только после проверки причины partial; отсутствие флага не выдаёт его
автоматически. Новый request-id нужен для новой операции, старый вернёт квитанцию.

Чтобы получить новые публикации в следующий день:

```sh
career-scout --workspace private/me run start --context CONTEXT_ID --plan PLAN_ID   --mode new --limits private/limits.json --env /absolute/path/jobs.env   --request-id collection-new-1
career-scout --workspace private/me run execute NEW_COLLECTION_RUN_ID --request-id collection-new-execute-1
```

Новый run фиксирует новое окно последних14дней. Выдача перечитывается, старые полные
описания повторно не скачиваются. Источники не получают одинаковую квоту вакансий:
число зависит от их выдачи и запросов. Уже сохранённое описание автоматически
не актуализируется при изменении карточки работодателем.

## Как понимать полноту

Полнота относится к сохранённому поисковому плану и доступной глубине выдачи.
Недоступная площадка или исчерпанный бюджет не доказывают отсутствие вакансий.
Карточки старше окна/без подтверждённой даты видны в диагностике отдельно;
дата изменения не подменяет дату публикации. Для HH большие окна делятся,
но полнота всего рынка всё равно не обещается.

[Анализ уже собранного](evaluation.md) · [Восстановление](../operations.md) ·
[Коды ошибок](../troubleshooting.md).
