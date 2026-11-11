# Полный план разработки Career Scout

Редакция 11.11.2026. План согласуется с существующими контрактами; это не реализация.
M0 выполнен, CS-001…CS-036 запланированы. Проверка API четырёх площадок выполнена
отдельно и не закрывает ни одну задачу production-адаптера.

## Продуктовый результат

Один запуск получает широкий релевантный пул вакансий из четырёх площадок,
сохраняет полные версии и обработанный остаток. Четыре профильных оценщика
последовательно выбирают первый подходящий вариант резюме. Владелец может
посмотреть основания, уточнить спорный случай, повторить обработку после изменения
резюме/условий и получить локальный экспорт. Перезапуск продолжает работу.

Критерии: охват плана прозрачен; partial и ошибки видны; текст не обрезан;
отказ по всем профилям только из четырёх валидных REJECT. Итоговый результат
содержит принятие, отказ, уточнение и незавершённость, не только top-N.

## Фичи MVP

| Фича | Пользовательское поведение | Задачи |
|---|---|---|
| Подключения | Проверить конфигурацию/доступ и причину недоступности каждого источника | CS-003…006,015…017,032 |
| Поисковый план | Посмотреть запросы четырёх направлений и лимиты до запуска | CS-007,008,018 |
| Полный сбор | Страницы, окна, карточки, повторные попытки, отражение недобора | CS-004,006,013,015…019 |
| История | Повторы не размножают карточки; изменения текста видны как новая версия | CS-002,009…011,019 |
| Продолжение | После остановки остались курсор, очередь и причина; можно продолжить | CS-011…013,025,031 |
| Предметные фильтры | IT/tech-функция, intern/junior/middle, no experience/1–3; unknown сохраняется | CS-014,028 |
| Профили | Загрузить четыре полных актуальных резюме, версии и явные атрибуты | CS-020,021 |
| Оценка | Получить короткое подтверждённое основание; первый PASS завершает цепочку | CS-022…025,029 |
| Уточнения | Посмотреть конкретный вопрос и повторно оценить после ответа | CS-026 |
| Итог | Увидеть source health, backlog, решения и расходы; экспортировать JSON/CSV | CS-032,033,035 |
| Хранение | Понимать срок доступности данных, expired state и правила очистки | CS-034,036 |

Настройки: source enable/budget, набор запросов, окно дат, concurrency вакансий,
TTL, фильтры, порядок профилей, model config и расходный лимит. Все версионируются.
Изменение настроек не должно требовать редактировать код адаптера.

## Границы

На этом этапе CLI, один локальный процесс и SQLite; LangGraph для оценки.
Auto-apply, изменения резюме, чат с работодателями, outreach, дополнительный бот,
публичный SaaS, UI, PostgreSQL/Kafka и автоматический scheduler за пределами MVP.
OpenBot может позже стать интерфейсом запуска/просмотра. Оценщик Sol — гипотеза
опционального аудита, не новая обязательная роль.

## Адаптация донора

[Подробная карта компонентов](component-adaptation.md) определяет точные файлы,
наши целевые места, зависимости и приёмку. HTTP/contracts/envelope и протоколы
трёх источников адаптируем. Parquet writer, file summaries/locks и stream заменяем.
HH, durable queue, filter/search planner и весь наш профильный маршрут пишем сами.
Текущее правило «не переносить весь radar» сохраняется.

## Релизы с полезным результатом

| Релиз | Состав | Что уже можно делать |
|---|---|---|
| R1: устойчивый HH collector | M1 + M2, применимые source/retention safeguards | Планировать, собирать, возобновлять и проверять полные вакансии HH |
| R2: источники | M3 | Собирать разрешённые источники, видеть их отдельную полноту; заблокированный источник не ломает остальные |
| R3: подбор по четырём резюме | M4 | Оценивать полный текст, обрабатывать уточнения и повторять по новым версиям |
| R4: принятый MVP | M5 + M6 | Использовать процесс с измеренным качеством, понятными расходами и runbook |

R1/R2 уже полезны без модели: можно проверить качество самой выборки.
R3 не считается готовым личным подбором до контрольной проверки M5.
SuperJob offline adapter можно разрабатывать при открытом вопросе хранения;
сохранение/LLM-передача его данных ждут ясного режима, без блокировки других источников.

## Этапы и условия завершения

### M0 — подготовка: выполнено

Repo/uv/CI, четыре исходные JSON Schema и synthetic examples, инструкции профилей,
базовые source/collector/storage/evaluation contracts, собственные ключи и bounded
search/detail check всех площадок. Сторонний runtime-код не переносился.

### M1 — контракты, transport и HH

CS-001…008. Сначала типизированные границы и нормализация, затем env/auth/HTTP и
официальный HH-адаптер. Императивные HTTP операции остаются за пределами LLM.
Конфигурация поиска становится immutable plan, а не цепочкой произвольных промптов.

Готово: offline cases 401/403/429/5xx/drift/cursor/cap; нет запроса после исчерпания
бюджета; plan не вызывает сеть; HH-карточка нормализована с ID и полнотой.
Узкий live check отдельно разрешён и содержит только read-only поиск/detail.

### M2 — устойчивый одноплощадочный процесс

CS-009…014. Сначала ledger/migrations и atomic page write, потом leases/detail
service и фильтры. Нужные source retention и безопасные логи делаются до live R1,
а не откладываются до последнего этапа; M6 закрывает общий жизненный цикл.

Готово: kill/restart на границах страницы/карточки не теряет записи и provenance;
stale detail не оценивается; видны частичная выдача, очередь и конкретные исключения.
Completed queries ≤ planned, хеш/версия и raw provenance разделены.

### M3 — три дополнительных адаптера

CS-015…019. Trudvsem, Habr и SuperJob имеют независимые capability/pagination fixtures.
Последовательность интеграции по готовности: Trudvsem, затем Habr; SuperJob
протокол можно делать отдельно, после M2. Учёт windows/fair budget общий, но
cursor/полнота/пределы и статус доступа остаются специфичными.

Готово: объединённый реестр без дублей одного source ID; у каждого источника свой
query progress; один blocked/partial не скрывается за успехом других; TTL refresh
не зависит исключительно от смены listing hash. Окна не дают обещания сплошного
охвата рынка. Выбранный запрос/параметры подтверждены source contract, не угаданы.

### M4 — четыре профиля и LangGraph

CS-020…026. Сначала актуальные тексты/рубрики и API модели, затем валидатор и граф.
Четыре логических оценщика могут использовать один параметризованный узел.
Не передавать предыдущие verdict в содержательный контекст следующего профиля.

Готово: first PASS прекращает вызовы; ERROR не отказ; quotes и входные версии
проверены; persistent checkpoint согласуется с ledger; кэш учитывает все версии.
Ответ владельца на вопрос создаёт атрибут/новую версию, не переписывает исходное
резюме без явного поручения. Предварительные order и model ID не маскируются default.

### M5 — пригодность и нагрузка

CS-027…031. Разделить tuning и контрольный набор. Подбирать не только «лёгкие»
совпадения: смежные названия, разные формулировки одного опыта, unknown, требования
в конце текста, ложные AI слова, mandatory vs nice, реальные недоступные карточки.

Готово: известны потери на search/filter/evaluation, ошибки каждого профиля,
стоимость/latency/backlog, результат аварийных сценариев. Порог качества утверждается
до подгонки по контрольному набору, иначе нельзя объявить процесс принятым.
Массовые сценарии сотен вакансий сначала offline/fake model, не стресс API площадки.

### M6 — эксплуатационная готовность

CS-032…036. Закончить status/explain/export, обслуживание и retention во всех слоях,
сводку запусков и runbook. Базовую диагностику/безопасность внедрять уже с M1/M2.
Подготовить повторяемую установку и walkthrough реальной работы без чтения чата.

Готово: запуск, остановка, продолжение, expired данные, бюджет/доступ и остаток
понятны владельцу. GitHub CI остаётся offline; живые проверки отдельны.
Автоматическое расписание не включается самим завершением MVP.

## Полный backlog

Ниже все задачи реализации. Поля dependencies задают технические предшественники;
source/model/owner prerequisites из следующего раздела действуют дополнительно.
Точные даты не назначены: они зависели бы от ещё непроверенной сложности переноса.

### CS-001 · Модели источников и ошибок (M1)

- Предшественники: нет.
- Результат: domain/source.py, domain/errors.py, JSON Schema.
- Готово, когда: SourceQuery/Page, typed cursor, capability map; drift отличим от пустой выдачи.
- Статус: planned.

### CS-002 · Нормализация и версии (M1)

- Предшественники: CS-001.
- Результат: normalization/{html,hashing,time,salary}.py.
- Готово, когда: Разделы/даты/gross-net/период/unknown сохранены; хеш устойчив к query provenance.
- Статус: planned.

### CS-003 · Конфигурация и секреты (M1)

- Предшественники: CS-001.
- Результат: config.py, env loader, config validation.
- Готово, когда: Читается единый .env; missing key диагностируется без печати значения.
- Статус: planned.

### CS-004 · HTTP и source budget (M1)

- Предшественники: CS-001, CS-003.
- Результат: collectors/transport.py, budget.py.
- Готово, когда: Finite retries, Retry-After seconds/date, cooldown общего источника, отказ за пределами бюджета.
- Статус: planned.

### CS-005 · HH application auth (M1)

- Предшественники: CS-004.
- Результат: collectors/auth/hh.py.
- Готово, когда: Рабочий токен переиспользуется; acquire атомарен; 401 не вызывает бесконечный цикл.
- Статус: planned.

### CS-006 · HH search/detail (M1)

- Предшественники: CS-002, CS-005.
- Результат: collectors/adapters/hh.py.
- Готово, когда: Search pages и full detail с identity; cap отмечен; snippets не проходят за full.
- Статус: planned.

### CS-007 · Планирование запросов (M1)

- Предшественники: CS-001, CS-003.
- Результат: collectors/planner.py, versioned search config.
- Готово, когда: Union четырёх семейств, title-only/AI-only не обязательны, fair ordering, immutable plan.
- Статус: planned.

### CS-008 · CLI plan и проверка конфигурации (M1)

- Предшественники: CS-003, CS-007.
- Результат: cli.py: plan, doctor.
- Готово, когда: Plan без сети; doctor различает отсутствие credentials, неподтверждённый order и disabled sources.
- Статус: planned.

### CS-009 · SQLite и миграции (M2)

- Предшественники: CS-001, CS-002.
- Результат: storage/migrations, connection.py.
- Готово, когда: WAL, foreign keys, schema version, транзакции на временной БД.
- Статус: planned.

### CS-010 · Snapshots и discoveries (M2)

- Предшественники: CS-009.
- Результат: storage/vacancies.py, discoveries.py.
- Готово, когда: Один source ID в нескольких запросах не дублируется; изменённый текст создаёт версию.
- Статус: planned.

### CS-011 · Прогресс query/window (M2)

- Предшественники: CS-009, CS-007.
- Результат: storage/progress.py.
- Готово, когда: Cursor меняется вместе с записью страницы; partial не продвигает чужой watermark.
- Статус: planned.

### CS-012 · Очередь и leases (M2)

- Предшественники: CS-009.
- Результат: storage/queue.py.
- Готово, когда: Atomic claim, конечный lease, retry_due; два исполнителя не владеют одной задачей.
- Статус: planned.

### CS-013 · Исполнитель сбора и detail (M2)

- Предшественники: CS-006, CS-010, CS-011, CS-012.
- Результат: services/collection.py, detail.py, cli collect.
- Готово, когда: Kill/restart не теряет данные; stale/deferred не уходит в evaluation.
- Статус: planned.

### CS-014 · Фильтры с основаниями (M2)

- Предшественники: CS-002, CS-010.
- Результат: domain/filtering.py, storage/filter_decisions.py.
- Готово, когда: Unknown/conflict сохранены; senior-коллега не означает senior-вакансию; версия фильтра.
- Статус: planned.

### CS-015 · Trudvsem адаптер (M3)

- Предшественники: CS-004, CS-013.
- Результат: collectors/adapters/trudvsem.py.
- Готово, когда: Offset 0/1/2, companycode+id, qualification/условия, complete/truncated.
- Статус: planned.

### CS-016 · Habr адаптер (M3)

- Предшественники: CS-004, CS-013.
- Результат: collectors/adapters/habr.py.
- Готово, когда: Frontend+HTML, SSR/JSON-LD identity, capped passes, partial/stale/blocked.
- Статус: planned.

### CS-017 · SuperJob адаптер (M3)

- Предшественники: CS-004, CS-013.
- Результат: collectors/adapters/superjob.py.
- Готово, когда: Собственный app key, more/page size, rich/fallback text, quota-like503.
- Статус: planned.

### CS-018 · Окна и общая справедливость (M3)

- Предшественники: CS-006, CS-007, CS-013.
- Результат: collectors/windows.py, scheduling.py.
- Готово, когда: Большой запрос не забирает весь budget; пересечения окон дедуплицируются; остаток виден.
- Статус: planned.

### CS-019 · Сверка обновлений и source acceptance (M3)

- Предшественники: CS-015, CS-016, CS-017, CS-018.
- Результат: services/reconciliation.py, live acceptance receipts.
- Готово, когда: TTL обновляет прежние listing; сбой источника даёт partial; доступ/retention отдельно.
- Статус: planned.

### CS-020 · Импорт резюме и профилей (M4)

- Предшественники: CS-003, CS-009.
- Результат: profiles/importer.py, registry.py.
- Готово, когда: Ровно четыре актуальных текста, hash/версия, universal/research исключены.
- Статус: planned.

### CS-021 · Рубрики и версия маршрута (M4)

- Предшественники: CS-014, CS-020.
- Результат: evaluation/policy.py, validated rubric config.
- Готово, когда: Вердикты однозначны; order подтверждён до evaluate; без скрытого обогащения биографии.
- Статус: planned.

### CS-022 · Модельный адаптер и бюджет (M4)

- Предшественники: CS-003, CS-004.
- Результат: evaluation/model.py, usage.py.
- Готово, когда: API model ID проверен, finite retries, timeout/context, tokens/cost_unknown, расходы ограничены.
- Статус: planned.

### CS-023 · Проверка результата (M4)

- Предшественники: CS-020, CS-021.
- Результат: evaluation/validation.py.
- Готово, когда: Schema, profile/version identity, цитаты в нужном тексте, без silent truncation.
- Статус: planned.

### CS-024 · Последовательный LangGraph (M4)

- Предшественники: CS-012, CS-021, CS-022, CS-023.
- Результат: evaluation/graph.py, routing.py.
- Готово, когда: First PASS прекращает вызовы; 256 сочетаний исходов и реальные call counts; persistent checkpoint.
- Статус: planned.

### CS-025 · Кэш и согласование checkpoint (M4)

- Предшественники: CS-010, CS-024.
- Результат: evaluation/cache.py, recovery.py.
- Готово, когда: Все входные версии в ключах; checkpoint сверяется с ledger; error не кешируется как reject.
- Статус: planned.

### CS-026 · Очередь уточнений (M4)

- Предшественники: CS-024, CS-025.
- Результат: services/review.py, cli review.
- Готово, когда: Показать основание/вопрос, ответ владельца версионируется; перепроверка затронутой пары.
- Статус: planned.

### CS-027 · Контрольная разметка (M5)

- Предшественники: CS-014, CS-021.
- Результат: private evaluation dataset + anonymized protocol.
- Готово, когда: Наборы настройки/контроля раздельны; по четырём профилям есть пограничные/негативные случаи.
- Статус: planned.

### CS-028 · Охват поискового плана (M5)

- Предшественники: CS-019, CS-027.
- Результат: evaluation report: search/filter losses.
- Готово, когда: Видно, на каком этапе потеряна каждая контрольная подходящая вакансия.
- Статус: planned.

### CS-029 · Качество оценщиков (M5)

- Предшественники: CS-025, CS-027.
- Результат: evaluation report per profile and route.
- Готово, когда: False rejects/accepts, clarification/error rates; отдельно по профилям; пороги согласованы.
- Статус: planned.

### CS-030 · Нагрузка и стоимость (M5)

- Предшественники: CS-019, CS-025.
- Результат: load report offline + bounded approved live.
- Готово, когда: Сотни synthetic карточек, fixed model concurrency, memory/backlog/usage; реальные квоты не стрессуются.
- Статус: planned.

### CS-031 · Сбои и восстановление (M5)

- Предшественники: CS-013, CS-025.
- Результат: failure acceptance suite.
- Готово, когда: Падение на границах HTTP/DB/LLM, duplicate pages, drift, exhausted budget не теряют очередь.
- Статус: planned.

### CS-032 · Статус и диагностика (M6)

- Предшественники: CS-013, CS-024.
- Результат: cli status, explain, source-health.
- Готово, когда: Отдельно list/detail/filter/eval и причины; blocked source не выглядит пустым рынком.
- Статус: planned.

### CS-033 · Локальный экспорт (M6)

- Предшественники: CS-026, CS-032.
- Результат: cli export JSON/CSV.
- Готово, когда: Версия/ссылка/профиль/основание и pending; без полного резюме по умолчанию.
- Статус: planned.

### CS-034 · Сроки хранения (M6)

- Предшественники: CS-010, CS-012, CS-025, CS-033.
- Результат: retention service + explicit maintenance command.
- Готово, когда: Удаление/истечение по policy охватывает raw, queue, checkpoint, export/backup; результат expired виден.
- Статус: planned.

### CS-035 · Логи и итог запуска (M6)

- Предшественники: CS-004, CS-013, CS-024.
- Результат: run summary, redaction, attempt usage.
- Готово, когда: Нет secrets/raw prompts; видны request counts, retry classes, backlog, cache hits, стоимость.
- Статус: planned.

### CS-036 · Инструкция запуска и приёмка MVP (M6)

- Предшественники: CS-028, CS-029, CS-030, CS-031, CS-032, CS-033, CS-034, CS-035.
- Результат: runbook + release acceptance.
- Готово, когда: Чистая установка, live receipts, recovery и измеренное качество; формальные неизвестные не скрыты.
- Статус: planned.


## Открытые решения и зависимости от владельца

| Решение | До какой задачи нужно | Что можно делать до него |
|---|---|---|
| Четыре свежих текста резюме | CS-020, реальная M4-оценка | Все collectors, storage и fake model tests |
| Порядок профилей | CS-021/024 live evaluate | Граф с тестовым порядком; order_confirmed=false |
| Рабочий API model ID/provider/effort/цена | CS-022 live smoke | Интерфейс и fake model; названия из OpenBot не подставлять автоматически |
| География/формат/оформление/доход | CS-014/021 если фильтр задан | null = нет ограничения; 120 net не вводить как общий cutoff |
| SuperJob storage/LLM transfer | CS-017/019 data persistence, реальная M4-передача | Адаптер/fixtures без рабочего архива, другие источники |
| Порог допустимых ложных отказов/принятий | CS-027 до контроля M5 | Подготовить разметку и отчёт без выдуманного целевого процента |

Это не запрос немедленно ответить на все вопросы. Они встроены в этапы;
при разработке независимые части не ждут этих решений.

## Проверяемые межкомпонентные границы

- collect: QueryPlan + SourceConfig → persisted snapshots/progress/report.
- detail: source identity + listing version → согласованный full/stale/unavailable.
- filter: snapshot + FilterVersion → keep/unknown/filtered_out с основанием.
- evaluate: full snapshot + one ResumeVersion + policy/model → validated verdict/error.
- route: order + служебные outcomes → accepted/rejected/clarification/pending.
- review: вопрос + явный ответ владельца → versioned attribute + targeted reevaluation.
- export: ledger + scope/retention → JSON/CSV и статистика, без выполнения внешних действий.

Новые схемы: SourceQuery/Page/Cursor/Error, Salary/Conditions, QueryProgress,
RunSummary, WorkItem/Attempt, ResumeVersion, RoutingRun и ReviewAnswer.
Текущие JSON Schema расширяются с новой версией при несовместимом изменении;
межполевые ограничения дополнительно в domain validators, а не только JSON types.

## План дерева исходников

```text
career_scout/
  cli.py, config.py
  domain/          # source, vacancy, filters, profile, evaluation, errors
  collectors/      # transport, auth, budgets, planner, windows, adapters
  normalization/   # html, time, salary, source mappings, hashes
  storage/         # migrations, snapshots, discoveries, query progress, queue
  services/        # collect, detail, reconciliation, review, export, retention
  evaluation/      # model, usage, validation, graph, routing, cache, recovery
tests/
  domain/, collectors/, storage/, evaluation/, services/, live/
```

Это план структуры; пустые модули ради вида не создаём. Протокол каждого слоя
узкий, без передачи DB-соединения или credentials всем подряд.

## Проверка готовности задачи и изменения

Каждая задача: реализация + meaningful offline tests + обновлённый контракт +
пройденный CI. Сетевой протокол дополнительно проходит bounded acceptance.
При адаптации — исходный SHA/license/изменения в NOTICE и карте переноса.
Завершённая задача меняет status в backlog.json и эту документацию.
Смена source API/правил может повторно открыть задачу.

Полезные проверки уже собраны в [acceptance.md](acceptance.md).
См. [карта переноса](component-adaptation.md), [архитектура](architecture.md),
[реальная текущая готовность](collector-readiness.md).
