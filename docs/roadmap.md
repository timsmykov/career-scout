# Roadmap: сначала полноценный коллектор

Редакция 11.11.2026 по уточнению владельца. Ближайшая цель — самостоятельный сервис
сбора, систематизации и хранения вакансий с передачей готовых данных будущему анализатору.
LangGraph, LLM-оценка, импорт резюме и настройка моделей отложены. Их существующие
контракты сохранены как будущий проект, не являются условиями готовности коллектора.

## Реализованные источники — 11.11.2026

Работают `plan/collect/resume/status/inspect/export/refilter/probe`, полный HH/Trudvsem/Habr detail и SuperJob probe, курсор
страницы в одной транзакции с discoveries, SQLite WAL/schema v4, дедупликация,
история содержательных версий, разбиение выдачи свыше 2000 по времени, finite
retries, request budget и сохранённый cooldown 429. Один процесс на БД через flock;
общих leases пока нет; SuperJob storage ожидает подтверждения режима. Запуск/перезапуск проверены offline и
ограниченным live-сбором. [Доказательства и ограничения](collector-readiness.md).
Ни один полный M1/M2 этап не объявлен завершённым только на основании HH-среза.

## Текущая оценка и следующий шаг — 11.11.2026

**HH core принят для одного процесса и ограниченного поискового плана.**
315 real full, 8 страниц, exact-ID dedup, настоящий SIGKILL/resume и Docker
volume recreation проверены; 300 synthetic карточек проходят container CI.
Это не приёмка четырёхплощадочного collector MVP и не доказательство охвата рынка.
[Объёмная приёмка](hh-volume-acceptance.md), [Docker](docker.md).

| Часть | Фактическая готовность | Остаток |
|---|---|---|
| HH auth/search/detail | Реализовано, CS-005/006 complete | Длительная эксплуатационная проверка |
| Страницы/версии/дочитывание | Работают для HH/Trudvsem/Habr; SJ код проверен synthetic | Source reconciliation, SJ допуск хранения |
| Размещение | Docker CLI + external SQLite volume | Backup policy/расписание — отдельная будущая потребность |
| Поиск/конфигурация | Валидируемый TOML, четыре семейства, география, frozen plan/resume | Doctor, измерение охвата |
| Условия/фильтры | Domain/grade/experience, unknown/conflict, зарплата нейтральна, история/refilter/export | Mappings других source, дальнейший контроль потерь |
| Другие источники | Trudvsem/Habr runtime и все4search/detail probes | SJ production storage пока выключен |
| Готовый набор | Текущий JSON export | CSV, закреплённые версии, manifest/cursor, expiry/retention |

Ближайший рекомендуемый порядок без моделей:

1. **Общий контракт/identity и базовые условия реализованы.** Common vacancy v2,
   `(source,source_id)`, composite Trudvsem ID, typed salary/experience/work formats,
   отдельные normalized projections и миграция рабочих317 snapshots без потерь.
   [Контракт и proof](normalization.md). Другие API mappings ещё не реализованы.
2. **Рабочий config/query planner и фильтры реализованы.** CS-003/007/014 complete,
   CS-008 partial (нет doctor). Четыре семейства, TOML, unknown/conflict, история
   решений и offline refilter; зарплата не фильтрует. [Контракт и проверки](filtering.md).
   Численность и лексические сигналы не считаются мерой соответствия резюме.
3. **Подключены Trudvsem/Habr; SuperJob ожидает режима хранения.** CS-015/016
   complete, CS-017/018/019 in_progress. Все четыре адаптера читают search/detail;
   пагинация, source budgets и restart общие. [Точный статус и proof](multi-source.md).
   Постоянный сбор SuperJob пока не принят; он явно blocked, остальные продолжают.
4. **Завершить передачу данных и эксплуатацию (CS-033/034/035/036/037):**
   фиксированные версии/экспорт, freshness/expiry/retention и контроль потерь.
   Многопроцессные leases нужны при введении параллельных исполнителей, а не
   только из-за появления Docker; сейчас действует один writer на ledger.

Пункты1–2 и Trudvsem/Habr из пункта3 реализованы. Остаток: условия сохранения
SuperJob, reconciliation, качество охвата и передача данных из пункта4.
LangGraph/оценщики/резюме остаются deferred. Статусы ниже синхронизированы с
backlog.json; in_progress означает частичную реализацию, не закрытый acceptance.

## Результат текущего этапа

Запуск собирает списки и полные карточки доступных источников, нормализует поля,
сохраняет версии и происхождение, устраняет точные дубли. После сбоя можно продолжить.
Владелец видит, что собрано полностью, что ожидает дочитывания и где доступ ограничен.
На выходе — сохранённый набор и локальный экспорт для последующего анализа.

Поток: поисковый план → страницы → полные карточки → нормализация → версии/дубли →
хранилище → контроль качества → готовый набор/экспорт. Лимиты и HTTP операции — код.
Поисковые семейства отражают четыре направления, но не требуют загрузки резюме.

## Фичи текущего MVP

| Фича | Результат |
|---|---|
| План поиска | Запросы, площадки, окна и бюджеты можно посмотреть до сети |
| Четыре адаптера | HH, Trudvsem, Habr, SuperJob с индивидуальными контрактами |
| Полные карточки | Отдельное дочитывание; missing/partial/stale не считаются готовыми |
| Систематизация | Company/title/text, зарплата и период/gross-net, опыт/грейд, локация/формат, даты и unknown/conflict |
| История | Source identity, content hash, версии и discovery provenance; новые условия не затирают старые |
| Продолжение | Курсор/окно и очередь фиксируются; повторы идемпотентны |
| Качество | Контроль пустых/сломанных полей, дублей, потерь текста и охвата плана |
| Доступ к данным | Status/inspect и JSON/CSV; стабильный cursor для будущего потребителя |
| Эксплуатация | Лимиты, retries, cooldown, retention и честный отчёт partial/full |

До моделей применяются только доказуемые предметные исключения и технические проверки.
Unknown сохраняется. Смысловое соответствие кандидату сейчас не вычисляется.

## Порядок релизов

1. **R1: устойчивый HH.** M1+M2: transport, контракты, HH auth/search/detail,
   SQLite, прогресс, очередь дочитывания, базовые status/inspect/export.
2. **R2: все источники.** M3: Trudvsem, Habr, SuperJob, окна/fair budgets,
   TTL обновления и сверка. Подключение источника зависит от его доступа/retention;
   недоступность одного не блокирует остальные.
3. **R3: принятый collector MVP.** Коллекторные части M5+M6: качество/охват,
   нагрузка, восстановление, экспортный контракт, retention и runbook.
4. **Будущий отдельный этап:** анализ вакансий по резюме. Технологию и порядок
   запуска обсуждаем после приёмки коллектора. Сейчас LangGraph не разрабатываем.

Нумерация M/CS сохранена для истории. M4 и задачи анализа помечены deferred.
Status/export/logging делаются по мере M2, а не откладываются до конца M6.

## Этапы

- **M0 выполнен:** repo/docs/окружение/CI и ограниченные live-проверки четырёх источников.
- **M1:** модели, нормализация, config, HTTP/budgets, HH auth/adapter и offline plan.
- **M2:** SQLite/migrations, snapshots/discoveries/progress, leases, collect/detail,
  фильтры с доказательствами; устойчивый одноплощадочный срез.
- **M3:** адаптация radar по источникам, окна и справедливое распределение бюджета,
  source reconciliation. См. [карту компонентов](component-adaptation.md).
- **M5, сбор:** контрольная выборка без резюме, потери поиска/фильтра, нагрузка и сбои.
- **M6, сбор:** status/inspect/export, сроки хранения, логи/отчёт и приёмка.
- **M4 и модельная часть M5:** deferred; не находятся на критическом пути.

## Передача будущему анализатору

Готовая запись содержит source+source_id, URL, title/company, полный нормализованный
текст, значимые условия, published/updated/fetched даты с точностью, content hash,
parser/normalizer version, provenance и признаки конфликтов/неизвестности.
Готовность — согласованный full snapshot, не archived/unavailable/expired, без
необработанной технической ошибки. Предметно исключённые записи сохраняют причину
и доступны отдельно, не стираются из истории.

Export/read interface: фиксированный cutoff, стабильная сортировка и cursor,
manifest schema_version/run_id/filter scope. Отдельно pending/deferred/stale,
показатели полноты. Если данные изменились после cutoff — новая версия/следующий
набор, не подмена записи во время чтения. SQL-запрос/локальный экспорт достаточно;
HTTP API или брокер без отдельной потребности не добавляем.

## Активный backlog

### CS-001 · Модели источников и ошибок (M1)

- Предшественники: нет.
- Результат: domain/source.py, domain/errors.py, JSON Schema.
- Готово, когда: SourceQuery/Page, typed cursor, capability map; drift отличим от пустой выдачи.
- Статус: in_progress.

### CS-002 · Нормализация и версии (M1)

- Предшественники: CS-001.
- Результат: normalization/{html,hashing,time,salary}.py.
- Готово, когда: Разделы/даты/gross-net/период/unknown сохранены; хеш устойчив к query provenance.
- Статус: in_progress.

### CS-003 · Конфигурация и секреты (M1)

- Предшественники: CS-001.
- Результат: config.py, env loader, config validation.
- Готово, когда: Читается единый .env; missing key диагностируется без печати значения.
- Статус: complete.

### CS-004 · HTTP и source budget (M1)

- Предшественники: CS-001, CS-003.
- Результат: collectors/transport.py, budget.py.
- Готово, когда: Finite retries, Retry-After seconds/date, cooldown общего источника, отказ за пределами бюджета.
- Статус: complete.

### CS-005 · HH application auth (M1)

- Предшественники: CS-004.
- Результат: collectors/auth/hh.py.
- Готово, когда: Рабочий токен переиспользуется; acquire атомарен; 401 не вызывает бесконечный цикл.
- Статус: complete.

### CS-006 · HH search/detail (M1)

- Предшественники: CS-002, CS-005.
- Результат: collectors/adapters/hh.py.
- Готово, когда: Search pages и full detail с identity; cap отмечен; snippets не проходят за full.
- Статус: complete.

### CS-007 · Планирование запросов (M1)

- Предшественники: CS-001, CS-003.
- Результат: collectors/planner.py, versioned search config.
- Готово, когда: Union четырёх семейств, title-only/AI-only не обязательны, fair ordering, immutable plan.
- Статус: complete.

### CS-008 · CLI plan и проверка конфигурации (M1)

- Предшественники: CS-003, CS-007.
- Результат: cli.py: plan, doctor.
- Готово, когда: Plan без сети; doctor различает отсутствие credentials, неподтверждённый order и disabled sources.
- Статус: in_progress.

### CS-009 · SQLite и миграции (M2)

- Предшественники: CS-001, CS-002.
- Результат: storage/migrations, connection.py.
- Готово, когда: WAL, foreign keys, schema version, транзакции на временной БД.
- Статус: complete.

### CS-010 · Snapshots и discoveries (M2)

- Предшественники: CS-009.
- Результат: storage/vacancies.py, discoveries.py.
- Готово, когда: Один source ID в нескольких запросах не дублируется; изменённый текст создаёт версию.
- Статус: complete.

### CS-011 · Прогресс query/window (M2)

- Предшественники: CS-009, CS-007.
- Результат: storage/progress.py.
- Готово, когда: Cursor меняется вместе с записью страницы; partial не продвигает чужой watermark.
- Статус: in_progress.

### CS-012 · Очередь и leases (M2)

- Предшественники: CS-009.
- Результат: storage/queue.py.
- Готово, когда: Atomic claim, конечный lease, retry_due; два исполнителя не владеют одной задачей.
- Статус: planned.

### CS-013 · Исполнитель сбора и detail (M2)

- Предшественники: CS-006, CS-010, CS-011, CS-012.
- Результат: services/collection.py, detail.py, cli collect.
- Готово, когда: Kill/restart не теряет данные; stale/deferred не уходит в evaluation.
- Статус: in_progress.

### CS-014 · Фильтры с основаниями (M2)

- Предшественники: CS-002, CS-010.
- Результат: domain/filtering.py, storage/filter_decisions.py.
- Готово, когда: Unknown/conflict сохранены; senior-коллега не означает senior-вакансию; версия фильтра.
- Статус: complete.

### CS-015 · Trudvsem адаптер (M3)

- Предшественники: CS-004, CS-013.
- Результат: collectors/adapters/trudvsem.py.
- Готово, когда: Offset 0/1/2, companycode+id, qualification/условия, complete/truncated.
- Статус: complete.

### CS-016 · Habr адаптер (M3)

- Предшественники: CS-004, CS-013.
- Результат: collectors/adapters/habr.py.
- Готово, когда: Frontend+HTML, SSR/JSON-LD identity, capped passes, partial/stale/blocked.
- Статус: complete.

### CS-017 · SuperJob адаптер (M3)

- Предшественники: CS-004, CS-013.
- Результат: collectors/adapters/superjob.py.
- Готово, когда: Собственный app key, more/page size, rich/fallback text, quota-like503.
- Статус: in_progress.

### CS-018 · Окна и общая справедливость (M3)

- Предшественники: CS-006, CS-007, CS-013.
- Результат: collectors/windows.py, scheduling.py.
- Готово, когда: Большой запрос не забирает весь budget; пересечения окон дедуплицируются; остаток виден.
- Статус: in_progress.

### CS-019 · Сверка обновлений и source acceptance (M3)

- Предшественники: CS-015, CS-016, CS-017, CS-018.
- Результат: services/reconciliation.py, live acceptance receipts.
- Готово, когда: TTL обновляет прежние listing; сбой источника даёт partial; доступ/retention отдельно.
- Статус: in_progress.

### CS-028 · Охват поискового плана (M5)

- Предшественники: CS-019, CS-037.
- Результат: Отчёт потерь search/filter.
- Готово, когда: Контрольные вакансии проверены на обнаружение и сохранение; оценка резюме не нужна.
- Статус: in_progress.

### CS-030 · Нагрузка коллектора (M5)

- Предшественники: CS-019.
- Результат: Offline load report и bounded live acceptance.
- Готово, когда: Сотни synthetic карточек, HTTP budgets, memory/backlog и длительность; без LLM.
- Статус: in_progress.

### CS-031 · Сбои и восстановление коллектора (M5)

- Предшественники: CS-013.
- Результат: Collector failure acceptance suite.
- Готово, когда: Падение HTTP/DB, duplicate pages, drift и exhausted budget не теряют очередь.
- Статус: in_progress.

### CS-032 · Статус и диагностика сбора (M6)

- Предшественники: CS-013.
- Результат: CLI status, inspect, source-health.
- Готово, когда: Видны list/detail/filter/expiry и причины; нет зависимости от графа оценки.
- Статус: in_progress.

### CS-033 · Экспорт готовых данных (M6)

- Предшественники: CS-032.
- Результат: CLI export JSON/CSV, manifest и курсор.
- Готово, когда: Полные согласованные версии с ID/hash/датами/provenance; partial/stale вне готового набора.
- Статус: in_progress.

### CS-034 · Сроки хранения коллектора (M6)

- Предшественники: CS-010, CS-012, CS-033.
- Результат: Retention service и maintenance command.
- Готово, когда: Expiry raw/snapshot/очереди/export/backup согласован; данные не попадают в готовый набор после истечения.
- Статус: planned.

### CS-035 · Логи и итог сбора (M6)

- Предшественники: CS-004, CS-013.
- Результат: Run summary и redaction.
- Готово, когда: Видны requests, retry, full/deferred/stale, backlog и полнота; secrets не логируются.
- Статус: in_progress.

### CS-036 · Приёмка collector MVP (M6)

- Предшественники: CS-028, CS-030, CS-031, CS-032, CS-033, CS-034, CS-035.
- Результат: Runbook и release acceptance.
- Готово, когда: Повторяемый сбор/перезапуск/экспорт; качество данных и охват проверены без модели.
- Статус: planned.

### CS-037 · Контрольная выборка для сбора (M5)

- Предшественники: CS-007, CS-014.
- Результат: Набор контрольных вакансий и правила проверки качества данных.
- Готово, когда: Проверены discovery, целостность текста, условия, дубли, версии и потери фильтра; резюме и LLM не нужны.
- Статус: planned.

## Отложенные задачи

- CS-020: Импорт резюме и профилей — deferred.
- CS-021: Рубрики и версия маршрута — deferred.
- CS-022: Модельный адаптер и бюджет — deferred.
- CS-023: Проверка результата — deferred.
- CS-024: Последовательный LangGraph — deferred.
- CS-025: Кэш и согласование checkpoint — deferred.
- CS-026: Очередь уточнений — deferred.
- CS-027: Контрольная разметка — deferred.
- CS-029: Качество оценщиков — deferred.

Свежие резюме, порядок профилей и model API понадобятся только будущему анализу.
У коллектора остаются собственные вопросы: поисковая география/период/бюджеты,
разрешённое хранение данных каждого источника, допустимые потери и качество полей.
Если условие владельца не задано, не придумывать фильтр; сохранять null/unknown.

## Критерии будущей приёмки collector MVP

Выполнены активные задачи; активные зависимости не ведут к deferred задачам.
Проверены pages/windows, full detail/ID, сохранение/возобновление, versions/dedup,
retention, экспортный cutoff/cursor и источник каждой записи.
Успех источника не скрывает недопройденные запросы или backlog карточек.
На контрольных примерах измерены потери discovery/filter и искажения текста/полей.
Нагрузка сотен synthetic карточек проверена offline; live-проверки bounded.
Нет требования подключать LLM, LangGraph или четыре резюме для этой приёмки.

[Backlog JSON](backlog.json), [архитектура](architecture.md),
[сборщик](collector.md), [текущая готовность](collector-readiness.md).
