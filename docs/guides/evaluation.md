# Анализ и подборка для откликов

Этот путь использует новый персональный контекст и полные результаты
[сбора](collection.md). Он не меняет сохранённые старые оценки владельца.

## Исполнитель и одно резюме

Codex должен быть установлен на хосте с действующим входом через ChatGPT.
Вызовы проходят через подписку, а не ключ model API. Выберите модель, доступную
вашей учётной записи; шаблон не гарантирует её доступность каждому пользователю.

Скопируйте [пример](../../examples/agent-connection.synthetic.json) в
`private/connection.json` и измените нужные поля. Для одного CV оставьте decisions=null
и allow_decisions_exclusion=false. Пример external_calls_enabled=false предотвращает
случайный запуск: разрешение конкретного run даётся отдельно.

```sh
career-scout --workspace private/me connection put default   --input private/connection.json --request-id model-connection-1
career-scout --workspace private/me connection show default
career-scout --workspace private/me doctor --connection default --env /absolute/path/jobs.env
```

Doctor — локальная проверка, auth_live_verified=false. В0.1 он проверяет полный
набор подключений и может сообщить ready=false без Decisions даже при допустимом
одном CV. Смотрите checks. Для нескольких CV нужен внешний совместимый native
коннектор; его контракт и workspace/root описаны в [справочнике](../context-evaluation.md).

## Prepare фиксирует входы, run выполняет

```sh
career-scout --workspace private/me evaluation prepare --context CONTEXT_ID   --collection-run COLLECTION_RUN_ID --limit 100 --max-calls 8 --max-decisions 0   --request-id evaluate-prepare-1
career-scout --workspace private/me evaluation run EVALUATION_RUN_ID --db DATABASE   --enable-calls --request-id evaluate-run-1
career-scout --workspace private/me evaluation status EVALUATION_RUN_ID --db DATABASE
```

DATABASE и EVALUATION_RUN_ID возвращает prepare. Одному CV при выключенном
Decisions-отсеве достаточно max-decisions0; при нескольких CV задайте конечный
бюджет маршрутизации и подключение. max-calls — число Codex-батчей с учётом попыток,
а не количество карточек. Фактические токены сохраняются, цена подписки не
превращается программой в автоматически рассчитанный API-счёт.

Каждый батч содержит одно резюме и до20независимых карточек; большие входы
не обрезаются ради лимита. Выбор двух резюме приводит к двум оценкам.
Настройки workers/batch_size и лимит символов фиксируются в подключении.

## Что попадает в подборку

```sh
career-scout --workspace private/me evaluation shortlist --db DATABASE --limit 20
career-scout --workspace private/me evaluation shortlist --db DATABASE --include-uncertain
career-scout --workspace private/me explain --analysis-db DATABASE --source hh --id VACANCY_ID
```

| Вердикт | Значение | Выдача |
|---|---|---|
| PASS | Подтверждена основа основной работы, нет существенного неразрешённого препятствия | Подборка по умолчанию |
| UNCERTAIN | Основная пригодность/обязательное условие неясны | Только с include-uncertain |
| REJECT | Есть конкретное существенное основание отказа | Сохранён в анализе; не в подборке |

Техническая ошибка — отдельное состояние. Вопросов к кандидату нет. Пустая
подборка может означать отсутствие PASS, устаревшие публикации или учёт истории;
сверьте status и explain. Подборка группируется по резюме, принимает cursor/limit;
ошибка cursor_stale означает изменение набора и требует чтения с начала.

Перед откликом проверьте оригинал вакансии. Программа не нажимает «Откликнуться».
Импорт личной истории с kind=applied скрывает уже откликнутое. Формат события
и разрешённые виды действий — в [справочнике](../context-evaluation.md).

## Остаток, продолжение и качество

Следующий prepare принимает следующий ранее не переданный остаток в версии
контекста. Для незавершённого run используйте resume; повторный prepare не
переоткрывает уже принятые карточки.

```sh
career-scout --workspace private/me evaluation resume EVALUATION_RUN_ID --db DATABASE   --additional-calls 2 --request-id evaluate-resume-1
career-scout --workspace private/me evaluation metrics EVALUATION_RUN_ID --db DATABASE
```

При нехватке Decisions отдельно задайте additional-decisions. Retry-errors
применяется только к известной технической ошибке после проверки её причины;
неизвестная доставка вызова блокирует автоматический повтор.

Без контрольных меток quality.measured=false. Для метрик передайте labels с
context_hash текущего анализа, cases source/source_id/expected и split, например
holdout. `career-scout schema --name labels` возвращает точную схему.
Затем `evaluation metrics ... --labels private/labels.json` вычисляет метрики
только этой разметки. [Границы текущих измерений](../product-audit-0.1.md).
