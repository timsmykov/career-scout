# Модули

Работают: hh.py (HTTP/auth/HTML normalizer), storage.py (SQLite v1),
collector.py (последовательный collect/resume), cli.py и __main__.py.
Ни один из них не импортирует LangGraph или модели.

Ниже сохранена целевая карта общего коллектора, а не список готовых импортов.

- collectors/: transport, source contracts, HH/Habr/Trudvsem/SuperJob adapters.
- domain/: vacancy, completeness, filter and evaluation models.
- storage/: SQLite migrations, repositories, leases, versioned snapshots.
- services/: collection, detail, refresh, quality, inspect/export.
- evaluation/: будущий отложенный анализ; сейчас не реализовывать.
- cli.py: явные команды plan / collect / status / inspect / export.

Добавлять модули по этапам docs/roadmap.md. Пустые фиктивные клиенты не нужны.
