# Архитектура коллектора и анализатора

Коллектор работает самостоятельно. По указанию владельца от12.11 анализ на
LangGraph развивается параллельно, используя уже готовые полные карточки.

Реализовано сейчас: HH transport/normalizer (`hh.py`), три новых адаптера (`adapters.py`),
общий HTTP (`transport.py`), source mappings (`source_normalization.py`), SQLite (`storage.py`),
campaign исполнитель (`campaigns.py`), legacy исполнитель (`collector.py`) и CLI (`cli.py`). Общая vacancy v2
и source identity — models.py, нормализация условий — normalization.py; SQLite v5
и lossless migration/backups — storage.py/migrations.py. [Контракт](normalization.md). Для одной БД
действует процессная блокировка; leases нескольких воркеров пока нет. Полная схема
ниже — целевая архитектура. Реализованы TOML/planner и фильтры без LLM; нет expiry/retention и стабильного
cursor экспорта; экспорт — согласованный текущий снимок SQLite. Подробности:
[development.md](development.md), [storage.md](storage.md), [multi-source.md](multi-source.md).

## Компоненты

1. Query planner: объединение поисковых семейств, source windows и порции HTTP без квот на число вакансий.
2. Source adapters: HH, Trudvsem, Habr и SuperJob, проверка capabilities/доступа.
3. Transport: конечные retries, timeout, cooldown, лимиты источника.
4. Collection/detail service: страницы, первое дочитывание и сохранённый cursor.
5. Normalization: полное содержание, условия, даты, unknown/conflict, source provenance.
6. SQLite ledger: identity, версии, discoveries, query progress и очередь задач.
7. Deterministic filters: domain/grade/experience, unknown/conflict, история решений;
   конфигурация и контракты — [filtering.md](filtering.md).
8. Quality/report/export: полнота, ошибки, готовый набор и интерфейс будущему анализу.

Историческая целевая схема коллектора; метка future analysis относится к раннему
плану. Реализованный сейчас анализ описан ниже. Expiry остаётся отложенным.

```text
plan → source pages → identity/discovery → detail queue
                                           ↓
                         full text → normalize → versioned storage
                                                    ↓
                                      quality/completeness/expiry
                                         ↓                  ↓
                                   ready dataset       pending/stale
                                         ↓
                                  inspect / export
                                         ↓
                                  future analysis
```

## Хранение и исполнение

Один процесс CLI, SQLite WAL на локальном диске. Короткие атомарные транзакции,
сеть вне транзакции; campaign/run/query и очередь первых detail сохранены в SQLite. Cursor сохраняется
вместе с результатом страницы. Повторная страница не создаёт дубли и не теряет
происхождение. Source rate budget общий для всех операций этого источника.

Каждый snapshot имеет content hash и parser/normalizer version. Метаданные
обнаружения и локальное время запроса не меняют смысловую версию.
В initial/new существующие listing/detail не изменяются. Читается список ID,
полный текст запрашивается только для ID без сохранённого описания.
Legacy collect сохраняет прежнее TTL/stale поведение для старых run IDs.
Сохранение списка, полнота карточек и готовность набора — разные состояния.

Нормализация не превращает неизвестные поля в нули, не обрезает текст,
не заменяет source даты временем скачивания. Grade/experience/technology filters
применяются только при достаточном основании, неоднозначность сохраняется.

## Граница передачи

Сейчас доступны согласованный JSON export campaign и read-only чтение каталога.
Передаются schema version, фиксированное окно публикации, IDs/hash и provenance.
CSV и стабильный consumer cursor остаются задачами дальнейшего интерфейса.
Missing/partial/stale/expired/archived записи не выдаются как готовые.
Незавершённый остаток и предметные исключения видны отдельно с причинами.
Коллектор не решает, подходит ли кандидат, и не зависит от наличия резюме.

## Анализ через подписку Codex

[Контракт и запуск](evaluation.md): `analysis.py` фиксирует полные входы из read-only
каталога, `evaluation.py` проверяет версии и основания, `analysis_graph.py` задаёт
порядок четырёх профилей, `codex_worker.py` вызывает native Codex CLI.
`analysis_store.py` хранит очередь, попытки, ответы и checkpoints в отдельной БД.

Порядок: Product → автоматизация → бизнес-ассистент → BA. Первый PASS завершает
маршрут; ERROR не превращается в отказ кандидату. У каждого вызова свежий
изолированный контекст с одним резюме. LLM-оркестратора и обязательного Sol нет.

Коллектор остаётся в Docker, анализатор запускается вручную на хосте с ChatGPT
login. Checkpointer использует отдельное SQLite-соединение: LangGraph сохраняет
состояние в фоне, соединение журнала результатов нельзя разделять с ним.
После сбоя проверенный ответ переиспользуется по ключу входов до нового вызова.
Возобновление не увеличивает бюджет автоматически. Leases нескольких исполнителей
и UI не реализованы; для одной analysis DB действует процессный flock.

[Полный текущий roadmap](roadmap.md).
