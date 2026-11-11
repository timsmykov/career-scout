# План модулей

Это карта реализации, не список готовых импортов.

- collectors/: transport, source contracts, HH/Habr/Trudvsem/SuperJob adapters.
- domain/: vacancy, completeness, filter and evaluation models.
- storage/: SQLite migrations, repositories, leases, versioned snapshots.
- evaluation/: LangGraph, worker inputs, model adapter, output validation.
- cli.py: явные команды plan / collect / evaluate / status / export.

Добавлять модули по этапам docs/roadmap.md. Пустые фиктивные клиенты не нужны.
