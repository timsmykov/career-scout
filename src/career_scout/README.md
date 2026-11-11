# Модули

Работают: hh.py (HTTP/auth/HTML normalizer), storage.py (SQLite v2),
collector.py (последовательный collect/resume), cli.py и __main__.py.
models.py/normalization.py задают общий контракт и HH mapping; migrations.py
сохраняет v1 backup и переносит базу, common.py — ошибки/хеши/clock.
Ни один runtime-модуль не импортирует LangGraph или модельный SDK.

Ниже сохранена целевая карта общего коллектора, а не список готовых импортов.

- collectors/: transport, source contracts, HH/Habr/Trudvsem/SuperJob adapters.
- domain/: vacancy, completeness, filter and evaluation models.
- storage/: SQLite migrations, repositories, leases, versioned snapshots.
- services/: collection, detail, refresh, quality, inspect/export.
- evaluation/: будущий отложенный анализ; сейчас не реализовывать.
- cli.py: явные команды plan / collect / status / inspect / export.

Добавлять модули по этапам docs/roadmap.md. Пустые фиктивные клиенты не нужны.
