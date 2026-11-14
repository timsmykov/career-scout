# CLI для агента

Установленная команда `career-scout` — JSON-интерфейс личной установки. Операции
находятся в общей Python-библиотеке `agent_service`/`agent_results`; будущему MCP
не требуется воспроизводить SQL или вызывать shell. Отдельного сервера и scheduler нет.

## Возможности и граница этапа

- Контекст: 1..100 полных резюме с произвольными ID, цели, свободные пожелания,
  дополнительные атрибуты, версии и происхождение данных.
- План: запросы агента, четыре адаптера, окно публикации, параметры площадок и
  явные фильтры. По умолчанию нет ограничений на профессию, отрасль, грейд или стаж.
- Сбор: очередь, фиксированное окно, постоянная SQLite, общий конечный HTTP-бюджет,
  порции, продолжение и чтение результатов с курсором.
- Существующий анализ: prepare/run/resume/status, чтение shortlist и explain.
  Его конфигурация по-прежнему задаётся прежним JSON с четырьмя профилями.

**Персональный контекст подключён к новому evaluation-пути**: динамические
Choices Decisions, оценка выбранной моделью с PASS/UNCERTAIN/REJECT, проверка пожеланий,
категории по резюме, история и метрики. `generic_context_evaluation=true`.
[Полный контракт и команды](context-evaluation.md). Описанные ниже analysis-команды
сохраняют прежний четырёхпрофильный режим; он явно возвращает
`agent_context_applied=false` и не подменяется новым контекстом.

## Установка и обнаружение команд

```sh
uv sync --locked --group dev
uv run --no-sync career-scout version
uv run --no-sync career-scout schema
uv run --no-sync career-scout schema --name context
uv run --no-sync career-scout --help
```

Эквивалент без установленного executable: `python -m career_scout.agent_cli`.
Старый `python -m career_scout` сохраняет прежний интерфейс коллектора и TOML;
его фильтры и рабочие данные не меняются. Новый CLI получает поиск из JSON-плана,
не подхватывает автоматически `configs/search.toml`. Публичный TOML нейтрален.

Полный список из 37 операций возвращает `schema`; параметры — `--help` команды.

| Область | Команды |
|---|---|
| Обнаружение и диагностика | `version`, `schema`, `provider list`, `doctor` |
| Контекст | `context create`, `context show`, `context update`, `context list` |
| Поисковый план | `plan put`, `plan show`, `plan preview`, `plan list` |
| Сбор | `run start`, `run execute`, `run resume`, `run status`, `run results`, `run list` |
| Подключение | `connection put`, `connection show` |
| Персональный анализ | `evaluation prepare`, `evaluation run`, `evaluation resume`, `evaluation status`, `evaluation shortlist`, `evaluation metrics` |
| История и квитанции | `history import`, `history list`, `operation show` |
| Прежний анализ | `analysis prepare`, `analysis run`, `analysis resume`, `analysis status` |
| Чтение подборки | `shortlist list`, `explain` |
| Навык агента | `skill install`, `skill show` |

Все операции, кроме help, печатают один JSON-объект в stdout, без вопросов и
неструктурированного прогресса. `--json` допустим, JSON уже является форматом
по умолчанию. Версия протокола — `schema_version: 1`. Успех команды — exit0,
ошибка команды — exit2. `run_status: partial` в успешном ответе означает, что
ограниченная операция выполнилась, но весь сбор ещё не завершён. Нужно смотреть
`run_status`, `report.sources`, остаток бюджета и ошибки источников.

Ответ: `status`, `result`, для изменений `operation_id`; при ошибке —
`error.code`, `retryable`, `outcome` и безопасные детали. Исключения не печатают
содержимое входов, ключей и credentials. `schema` возвращает список операций,
capabilities и актуальные JSON Schema входов; опубликованные копии находятся в
[schemas](../schemas/agent-context.schema.json).

## Контекст и поисковый план

Вымышленные входы:
[контекст](../examples/agent-context.synthetic.json),
[план](../examples/agent-plan.synthetic.json),
[лимиты](../examples/agent-limits.synthetic.json).

```sh
career-scout --workspace private/demo context create \
  --input examples/agent-context.synthetic.json --request-id demo-context-1
career-scout --workspace private/demo context show CONTEXT_ID --summary
career-scout --workspace private/demo context show CONTEXT_ID --version 1
career-scout --workspace private/demo plan put --context CONTEXT_ID \
  --input examples/agent-plan.synthetic.json --request-id demo-plan-1
career-scout --workspace private/demo plan preview PLAN_ID
```

`--input -` читает JSON из stdin. Максимальный вход — 16MB; повторяющиеся ключи
JSON и нечисловые NaN/Infinity запрещены. `resumes[].text` сохраняется полностью,
без пересказа и очистки авторского текста. ID резюме — безопасная строка длиной
до64символов; специальность не является enum. Пожелание — строка или объект
`{statement, priority, provenance, structured}`. Приоритет: required/preferred/avoid.
Неизвестный факт остаётся неизвестным, CLI его не дополняет.

В квитанциях `context create` возвращает `result.context_id`, а `plan put` —
`result.id`: это значение подставляется вместо PLAN_ID. `run start` возвращает
`result.run_id`; `evaluation prepare` — отдельные `result.run_id` и `result.database`.

Изменение — JSON с заменяемыми полями верхнего уровня, например
`{"goals":"Новая цель","preferences":["Только удалённо"]}`:

```sh
career-scout --workspace private/demo context update CONTEXT_ID \
  --input private/patch.json --if-version 1 --request-id demo-update-1
```

Массив `resumes`/`preferences` заменяется целиком, скрытого merge нет. Версия
сравнивается атомарно: конкурирующая запись получает `version_conflict`.
Неизменный документ не увеличивает версию. Старые версии доступны для чтения.
План с прежней версией нельзя запустить после изменения контекста:
`plan_context_stale`; агент сохраняет новый план. Уже созданный запуск остаётся
на своей версии и показывает `context_is_newer`, не меняет правила посреди прохода.

План принимает `sources`, `queries`, `days` (по умолчанию14), `per_page` (до100),
`source_options`, `area_ids` и `collection_policy`. Для HH география — area_ids;
SuperJob — town_id, Habr — city_id, Trudvsem — region_code. Чужие идентификаторы
и синтаксис адаптеры не переносят между площадками. Habr по умолчанию имеет
интервал3секунды. Для SuperJob перед постоянным сбором требуется явное
`source_options.superjob.storage_allowed=true`.

`collection_policy` предназначена для известных формализованных условий:
допустимых грейдов, категорий опыта, максимального стажа, отраслевых терминов и
явных исключений. `max_required_years:null` и пустые domain_terms снимают эти
ограничения; неизвестные значения сохраняются. Зарплата в этом фильтре необязательна.
Произвольный текст пожелания не превращается в жёсткий фильтр автоматически.
Запросы передаются выбранным адаптерам; составление хороших гипотез остаётся
работой агента. `plan preview` не делает HTTP и не создаёт collector DB.

## Очередь и ограниченный сбор

```sh
# Сохранить намерение; HTTP ещё нет
career-scout --workspace private/demo run start --context CONTEXT_ID --plan PLAN_ID \
  --limits examples/agent-limits.synthetic.json --env /absolute/path/credentials.env \
  --request-id demo-start-1
# Выполнить конечную порцию в текущем процессе
career-scout --workspace private/demo run execute RUN_ID --request-id demo-execute-1
career-scout --workspace private/demo run status RUN_ID
career-scout --workspace private/demo run resume RUN_ID --request-id demo-resume-1
# Явно увеличить общий бюджет, если он исчерпан
career-scout --workspace private/demo run resume RUN_ID --additional-requests 200 \
  --request-id demo-resume-2
career-scout --workspace private/demo run results RUN_ID --limit 20
```

`max_requests` — **общий** предел HTTP-попыток всего запуска, включая retries и
все источники. Он сохраняется до обращения к источнику. `chunk_requests` — предел
порции одного источника; `max_rounds` — число обходов выбранных источников за
одну команду execute/resume. Возобновление не выдаёт новый бюджет автоматически;
`--additional-requests` увеличивает его явно. Одновременно один запуск выполняет
один процесс под файловой блокировкой. Второй получает `run_busy`.

`run start --mode initial|new` фиксирует одинаковое окно последних N дней по
публикации. Следующий ручной `new` — новый run с новым request-id и свежим окном;
он использует общую collector DB workspace, скачивает описания только новых ID.
`resume` продолжает старое окно и существующие checkpoints. Недоступные/старые/
date-unknown карточки видны в диагностике по прежнему контракту коллектора.
Площадки с 401/403/429 остаются в своих состояниях; обхода ограничений нет.

Команда работает в вызывающем процессе: фонового daemon здесь нет. При внешнем
прерывании долговечные checkpoints сохраняются; `run status` отличает занятый
lock от `interrupted`. Для прогресса отчёт читается из collector через read-only
snapshot (`report_origin=collector_snapshot`); до появления кампании используется
сохранённый checkpoint. Длительный запуск не требует удержания сессии агента:
после остановки достаточно сохранённого run_id и нового resume.

По умолчанию workspace — `$XDG_DATA_HOME/career-scout` или
`~/.local/share/career-scout`. `--workspace` можно передавать до или после команды.
Там находятся `control.sqlite3`, `collector.sqlite3`, `run-locks/`, `auth/`.
Новый каталог приватный0700, control DB0600. Это личная установка, без tenant IDs.
Чтение не создаёт отсутствующий workspace и не мигрирует чужую БД.

`--env` — ссылка на существующий credential-файл, содержимое не копируется в
контекст, план, SQLite и ответ CLI. При переносе можно передать новую ссылку в
`run resume --env ...`; существующие провайдеры/логины не меняются. По умолчанию
используется `.env` внутри workspace. HH token cache хранится отдельно в auth/.
Для Codex остаётся существующая подписочная авторизация на хосте.

## Повторные команды и неопределённый результат

Изменяющие workspace команды требуют `--request-id`; `skill install` идемпотентен
по содержимому и не использует квитанции workspace. Повтор с тем же ID и теми же
аргументами возвращает **первоначальную квитанцию**, без повторного выполнения.
Другие аргументы с тем же ID дают `request_id_conflict`. Это не команда обновления
статуса: за актуальным состоянием нужно обращаться к `run status`/`analysis status`.

```sh
career-scout --workspace private/demo operation show demo-execute-1
```

Если процесс погиб после сохранения намерения, но до квитанции,
`operation_incomplete/outcome=unknown` не означает отсутствие действий. Агент
читает operation и run status; для продолжения **того же** запуска использует
новый request-id. Зафиксированный campaign_id исключает создание второго
коллектора при таком восстановлении. Внешний HTTP, прерванный до сохранения
ответа, иногда приходится повторить; гарантия касается локальных сохранённых
данных и бюджетов, а не exactly-once доставки по сети.
Для анализа действуют также существующие ограничения unknown-delivery модельных
батчей: новый request-id не разрешает автоматически повторять неизвестный вызов.

## Существующий анализ и итоговая подборка

```sh
career-scout --workspace private/control analysis prepare --config private/analysis.json \
  --limit 20 --max-calls 4 --request-id analysis-prepare-1
career-scout --workspace private/control analysis run ANALYSIS_RUN_ID \
  --db data/analysis.sqlite3 --request-id analysis-run-1
career-scout analysis status ANALYSIS_RUN_ID --db data/analysis.sqlite3
career-scout shortlist list --analysis-db data/analysis.sqlite3 --limit 20
career-scout explain --analysis-db data/analysis.sqlite3 --source hh --id VACANCY_ID
```

prepare фиксирует входы без вызова модели. run/resume требуют существующего
явного разрешения в analysis-конфигурации; `external_calls_enabled=false` блокирует
модели. Модельные вызовы ограничены `max-calls`, Decisions — отдельным бюджетом в
прежнем routing-конфиге. Эта обвязка не обходит прежние бюджеты/политику повторов.
Четыре профиля и критерии существующего анализа не заменяются новым контекстом.
Для произвольного контекста используйте новый [evaluation-путь](context-evaluation.md).
Его программная работоспособность и точность на рынке измеряются отдельно.

shortlist читает только текущие рекомендации с учётом исключений, откликов и
актуальности. explain отдельно читает сохранённые оценки/основания и историю.
Эти команды не пересчитывают модели, не меняют БД и не перезаписывают выгрузки.
`--profile` фильтрует существующие результаты, не создаёт новый профиль.

Списки context/plan/run, results и shortlist принимают `--limit`1..200 и `--cursor`.
Ответ содержит items, total, next_cursor. Курсор связан с содержимым набора;
при изменении данных — `cursor_stale`, нужно начать чтение заново. Shortlist
ограничен10000записями и сообщает truncated, если достигнута эта граница.
Результаты сбора краткие; `run results --full` включает полные карточки.

## Docker и проверка

Существующий Docker entrypoint остаётся коллектором. Новый интерфейс доступен в
том же image отдельным установленным executable; например, без сети:

```sh
docker run --rm --network none --entrypoint /app/.venv/bin/career-scout \
  career-scout:local schema --name context
```

Для изменяющих команд нужен отдельный постоянный каталог/volume и --workspace.
Подписочную авторизацию в image не помещать.

`tests/test_agent_cli.py` проверяет команды через main, синтетический HTTP,
версии/конкуренцию, квитанции, budgets/resume, stdin/ошибки, чтение прежней БД и
запрет модельных вызовов. `scripts/check_container.py` проверяет установленный
executable в image и сохранение синтетических данных после пересоздания контейнера.

## Навык и каталог провайдеров

```sh
career-scout provider list
career-scout skill show
career-scout skill install --agent codex
career-scout skill install --agent claude --directory /path/to/skills
career-scout --workspace private/me doctor --context CONTEXT_ID
```

`skill install/show` и `provider list` работают до создания workspace. Установка
идемпотентна по содержимому, сохраняет отличающуюся папку; request-id не требуется.
Остальные workspace-мутации сохраняют прежний контракт request-id.
[Навык](agent-skill.md), [провайдеры](model-providers.md). `--env` у evaluation
run/resume задаёт runtime-ссылку на model credentials; ключи не входят в connection
или квитанцию. `doctor` проверяет настройку оценщика; источники/живой вход отдельно.
