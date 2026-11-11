# План модулей

Это карта реализации, не список готовых импортов.

- collectors/: transport, source contracts, HH/Habr/Trudvsem/SuperJob adapters.
- domain/: vacancy, completeness, filter and evaluation models.
- storage/: SQLite migrations, repositories, leases, versioned snapshots.
- services/: collection, detail, refresh, quality, inspect/export.
- evaluation/: будущий отложенный анализ; сейчас не реализовывать.
- cli.py: явные команды plan / collect / status / inspect / export.

Добавлять модули по этапам docs/roadmap.md. Пустые фиктивные клиенты не нужны.
