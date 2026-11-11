# Архитектура текущего этапа: коллектор

Сначала самостоятельный сервис сбора, систематизации и хранения. LangGraph и
оценка по резюме отложены: коллектор должен быть полезен и принят без них.

Реализовано сейчас: HH transport/normalizer (`hh.py`), SQLite (`storage.py`),
последовательный исполнитель (`collector.py`) и CLI (`cli.py`). Для одной БД
действует процессная блокировка; leases нескольких воркеров пока нет. Полная схема
ниже — целевая архитектура. В первом срезе нет фильтров, expiry/retention и стабильного
cursor экспорта; экспорт — согласованный текущий снимок SQLite. Подробности:
[development.md](development.md), [storage.md](storage.md).

## Компоненты

1. Query planner: объединение поисковых семейств, source windows и fair budgets.
2. Source adapters: HH, Trudvsem, Habr и SuperJob, проверка capabilities/доступа.
3. Transport: конечные retries, timeout, cooldown, лимиты источника.
4. Collection/detail service: страницы, дочитывание, обновления и сохранённый cursor.
5. Normalization: полное содержание, условия, даты, unknown/conflict, source provenance.
6. SQLite ledger: identity, версии, discoveries, query progress и очередь задач.
7. Quality/report/export: полнота, ошибки, готовый набор и интерфейс будущему анализу.

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
сеть вне транзакции, claim/lease для задач detail/refresh. Cursor сохраняется
вместе с результатом страницы. Повторная страница не создаёт дубли и не теряет
происхождение. Source rate budget общий для всех операций этого источника.

Каждый snapshot имеет content hash и parser/normalizer version. Метаданные
обнаружения и локальное время запроса не меняют смысловую версию.
Listing и detail согласованы; старый detail после изменения списка — stale.
Сохранение списка, полнота карточек и готовность набора — разные состояния.

Нормализация не превращает неизвестные поля в нули, не обрезает текст,
не заменяет source даты временем скачивания. Grade/experience/technology filters
применяются только при достаточном основании, неоднозначность сохраняется.

## Граница передачи

Готовая версия доступна через ledger/read interface и JSON/CSV export.
Есть schema version, фиксированный cutoff, IDs/hash, provenance и cursor.
Missing/partial/stale/expired/archived записи не выдаются как готовые.
Незавершённый остаток и предметные исключения видны отдельно с причинами.
Коллектор не решает, подходит ли кандидат, и не зависит от наличия резюме.

## Будущий анализ

Ранее подготовленные [контракты оценки](evaluation.md) сохраняются как отложенные.
Model calls, import resumes, first-PASS routing и LangGraph не относятся к текущей
реализации/приёмке. Текущей очереди достаточно задач collect/detail/refresh/export;
не требуются model checkpoints или постоянно работающие агенты.

[Полный текущий roadmap](roadmap.md).
