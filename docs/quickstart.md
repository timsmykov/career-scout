# Первый личный запуск 0.1.0

Инструкция относится к публичной ветке main. Требуются Linux,
Python3.12, uv, собственный доступ к выбранной площадке и установленный на хосте
Codex с рабочим входом через ChatGPT. Career Scout не устанавливает Codex и не
копирует авторизацию. [Ограничения и результаты приёмки](product-audit-0.1.md).

Начните с одного резюме: Decisions для этого сценария не нужен. Все изменяющие
команды требуют уникальный request-id; тот же ID с теми же аргументами возвращает
прежнюю квитанцию. Секреты и реальные тексты храните только в `private/` или
отдельном личном каталоге.

## Установка и входы

Из корня полученного репозитория:

```sh
uv sync --locked
. .venv/bin/activate
career-scout version
career-scout schema
umask 077
mkdir -p private/me
```

Создайте `private/context.json` со **своим полным резюме**. Минимальная форма:

```json
{
  "resumes": [{"id": "my_resume", "title": "Название резюме", "text": "Полный текст резюме"}],
  "goals": "Какие задачи и работу ищу",
  "preferences": [{"statement": "Удалённая работа", "priority": "preferred"}]
}
```

Приоритет required означает обязательное условие; unknown по такому пожеланию
не допускает PASS. preferred допускает компромисс. Зарплату без указанной суммы
не нужно исключать. Код не подставляет скрытые профессиональные ограничения.

Сохраните запросы в `private/plan.json`, например:

```json
{"sources": ["hh"], "queries": ["инженер интеграций", "специалист автоматизации"], "days": 14}
```

Запросы выше — пример, их необходимо заменить под своё резюме. Credentials
выбранных площадок задайте в личном env-файле по [.env.example](../.env.example).
Для остальных источников есть [отдельные контракты](sources/README.md).
Для постоянного сбора SuperJob требуется явный storage_allowed и своё разрешение
на хранение данных. Не используйте чужие доступы.

## Сбор

```sh
career-scout --workspace private/me context create \
  --input private/context.json --request-id context-1
career-scout --workspace private/me plan put --context CONTEXT_ID \
  --input private/plan.json --request-id plan-1
career-scout --workspace private/me plan preview PLAN_ID
career-scout --workspace private/me run start --context CONTEXT_ID --plan PLAN_ID \
  --limits examples/agent-limits.synthetic.json --env /absolute/path/jobs.env \
  --request-id collect-start-1
career-scout --workspace private/me run execute COLLECTION_RUN_ID --request-id collect-execute-1
career-scout --workspace private/me run status COLLECTION_RUN_ID
```

CONTEXT_ID, PLAN_ID и COLLECTION_RUN_ID берутся из JSON предыдущей команды.
В примере общий HTTP-бюджет200, порция50; это конечная проба, не обещание полного
обхода. Статус partial требует продолжения и проверки ошибок источников:

```sh
career-scout --workspace private/me run resume COLLECTION_RUN_ID \
  --additional-requests 200 --request-id collect-resume-1
career-scout --workspace private/me run results COLLECTION_RUN_ID --limit 20
```

За следующим ручным добавлением создайте новый run start с `--mode new`, тем же
планом и новым request-id. Описания сохранённых ID повторно не скачиваются.

## Анализ и подборка

Скопируйте [пример подключения](../examples/agent-connection.synthetic.json)
в приватный JSON и выберите доступную вам модель Codex. В примере реальные
вызовы выключены; `--enable-calls` ниже явно включает конкретный запуск.

```sh
cp examples/agent-connection.synthetic.json private/connection.json
# Измените private/connection.json под доступную модель до connection put.
career-scout --workspace private/me connection put default \
  --input private/connection.json --request-id connection-1
career-scout --workspace private/me evaluation prepare --context CONTEXT_ID \
  --collection-run COLLECTION_RUN_ID --limit 20 --max-calls 2 --max-decisions 0 \
  --request-id prepare-1
career-scout --workspace private/me evaluation run EVALUATION_RUN_ID --db DATABASE \
  --enable-calls --request-id evaluate-1
career-scout --workspace private/me evaluation status EVALUATION_RUN_ID --db DATABASE
career-scout --workspace private/me evaluation shortlist --db DATABASE
career-scout --workspace private/me evaluation metrics EVALUATION_RUN_ID --db DATABASE
```

EVALUATION_RUN_ID и DATABASE вернёт prepare. Бюджет2 — максимум Codex-батчей,
не цена и не число вакансий; ошибка ответа может занять попытку. Prepare не
вызывает модели. При одном CV и выключенном автоотсеве Decisions-бюджет0 допустим.
Предварительно доступная модель и авторизация должны быть проверены владельцем.

В shortlist по умолчанию только PASS. `--include-uncertain` добавляет завершённые,
но спорные оценки; система не превращает их в вопросы к вам. Рекомендации могут
отсутствовать — это не ошибка команды. Качество без контрольной разметки отмечено
как неизмеренное. [Вердикты, история, метрики и продолжение](context-evaluation.md).

Следующий prepare принимает следующий остаток в том же контексте. Для незавершённого
анализа используйте evaluation resume с сохранёнными run_id/database; новый
prepare не заменяет возобновление. Не увеличивайте бюджеты автоматически в цикле
без проверки причины partial/error.

## Проверка установки без внешних вызовов

```sh
docker compose build
python3 scripts/check_container.py
python3 scripts/check_product.py
```

Обе проверки изолированы от рабочих данных; первая проверяет постоянное хранение,
вторая — полный сценарий через установленный пакет. В ней HTTP и Codex эмулируются,
поэтому она не подтверждает живую авторизацию или смысловое качество модели.
Рабочий анализ запускайте на хосте: в стандартный Docker-образ Codex не включён.
