# План реализации

## M0 — Bootstrap: выполнено

Документация, source references, JSON Schema, вымышленные примеры, конфигурации,
инструкции четырёх оценщиков, uv environment/lock и offline CI.
Не означает реализацию сборщиков/агентов.

## M1 — Контракты и HH read-only

Модели domain, httpx transport, auth injection, finite budgets, typed errors,
HH search/detail, fixtures с повторной страницей/403/429/плохой схемой.
Не читать/менять резюме через HH API. Критерий: offline tests + отдельный bounded
live read при явно подключённом доступе, корректный partial status.

## M2 — Сохранение и возобновление

SQLite migrations, snapshots/discoveries/query_progress, очередь detail/evaluate,
leases, schema version, expiry. Критерий: kill/restart после записи страницы
не теряет и не дублирует карточки; stale detail не считается full.

## M3 — Другие источники

Trudvsem → Habr frontend/HTML → SuperJob после выполнения access/storage prerequisites.
Каждый независим: capability mapping, pagination fixtures, budgets, schema drift,
quota errors, complete vs deferred. Не ждать SuperJob для завершения HH/Trudvsem.
Донорские модули переносить только с attribution и адаптационными тестами.

## M4 — LangGraph и четыре профиля

Получить актуальные резюме, утвердить порядок, проверить модель API.
Внедрить JSON Schema, цитаты, role rubrics, конечные попытки, persistent checkpoint,
ledger/cache keys. Критерии docs/acceptance.md, в том числе все 256 сочетаний
PASS/BORDERLINE/REJECT/ERROR для четырёх позиций и фактическая остановка вызовов.

## M5 — Проверка качества

Владелец размечает реальные полные вакансии по каждому профилю и общему маршруту.
Разделить набор настройки и контрольный. Измерить false rejects, false accepts,
неопределённость, трудозатраты проверки, стоимость и backlog. Порог приемлемого
качества утвердить до заявления «система готова». Объём сотни карточек — нагрузка,
не доказательство качества. Никаких выдуманных процентов пригодности.

## M6 — Эксплуатация

Локальный экспорт, run summary, диагностические команды, политика retention.
Только после live acceptance обсуждать schedule/OpenBot interface/UI.
Нет автоматического перехода в автоотклики или редактирование резюме.
