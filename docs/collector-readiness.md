# Доступ к площадкам и готовность коллектора

Проверено 11 ноября 2026 (Europe/Moscow).
Доказательства: [обезличенный HTTP-отчёт](references/live-checks-20261111.json).
Значения ключей и тексты вакансий в отчёт не записаны.

## Что реально проверено

| Площадка | Поиск | Detail | Основание |
|---|---|---|---|
| HH | 200, 2 карточки | 200, 1542 символа HTML description | /me: is_application=true; ID совпадает |
| SuperJob | 200, 2 карточки | 200, 3786 символов текстовых полей | Собственный app key 5273; ID совпадает |
| Работа России | 200, 2 карточки | 200, 251 символ duty/qualification | Body status 200, identity совпадает |
| Habr Career | 200, 25 карточек | 200, 2796 символов description | JSON-LD JobPosting и SSR присутствуют |

HH client ID + client secret дополнительно проверены получением application token
через client_credentials и запросом /me с новым токеном: оба HTTP 200.
Новый токен сохранён в единый локальный .env. Для SuperJob проверен app key,
OAuth access token пользователя для этого поиска не потребовался.

Habr использует frontend JSON и HTML, остальные три — официальные API.
В запросах не менялись proxy/IP/account. Каждая площадка получила один поиск
и одну карточку; Habr вернул свой размер страницы 25. Ничего не отправлялось в LLM.

Эта проверка доказывает текущую доступность поиска и содержимого примера.
Она не проверяет все страницы, долгую нагрузку, пределы окон, фильтры, повторные
ошибки или семантическую пригодность. Длины — характеристика ответа, не метрика
полноты по смыслу. Статус API после будущих изменений требует новой проверки.

## Полноценен ли текущий коллектор

Реализован первый HH-срез: поиск, полные карточки, SQLite WAL, snapshots/discoveries,
пагинация, сохранённые окна, finite retries, request budget, source cooldown,
CLI plan/collect/resume/status/inspect/export. Основные модули: hh.py, storage.py,
collector.py, cli.py. Это первый источник, а не готовый четырёхплощадочный MVP.

[Живой HH collector acceptance](references/hh-collector-live-20261111.json):
первый запуск с budget=1 сохранил страницу/2 ID и оставил 2 detail pending.
Второй процесс resume с budget=4 дочитал карточки и следующую страницу: суммарно
2 страницы/4 ID/3 full/1 deferred; статус partial по бюджету, cursor=2.
Отдельный узкий двухсекундный план завершён: 2 страницы, 3 уникальных full карточки,
0 deferred/truncated/rejected, complete. Для него понадобилось 4 HTTP-запроса,
одна карточка использована из свежего локального snapshot; финальный повтор двух страниц новой версией клиента добавил 2 HTTP, всего 11 HTTP.
Полный текст и HTML сверены с исходным detail, JSON export прочитан из SQLite.
Реальные тексты и БД находятся только в gitignored data/, отчёт содержит метаданные.

Offline pytest проверяет транзакционный rollback, аварийный выход subprocess,
продолжение без повторного поиска committed page, версии/TTL, date splitting,
401/403/429, schema drift и CLI. Эти сценарии не выдаются за live нагрузочный тест.
[Команды и точные границы](development.md). Существующий OpenBot Scout не менялся.

## Проверка на сотнях вакансий

[Полный отчёт](hh-volume-acceptance.md): сотни полных HH карточек, два пересекающихся
запроса, принудительный SIGKILL, продолжение новым процессом, payload/SQLite/JSON аудит.
Результат и точные счётчики отделены от проверки широкого охвата рынка.

## Docker и сохранность

HH CLI упакован в отдельный образ, SQLite хранится в external volume
`career-scout-data`. Прежние 315 snapshots сохранены при миграции через backup;
реальный HH run продолжился после удаления первого контейнера. Container CI
дочитывает 300 synthetic карточек между двумя отдельными контейнерами без сети.
[Размещение, команды и evidence](docker.md). Общий collector MVP остаётся незавершённым;
[актуальная оценка и следующий шаг](roadmap.md).

## Остаток по площадкам

| Площадка | Что нужно реализовать/проверить |
|---|---|
| HH | Первый runtime реализован; остаются общий config/query planner, типизированные cross-source условия/фильтры, watermark reconciliation, retention и длительная эксплуатационная приёмка |
| SuperJob | App key injection, фактический page size, more/cap, окна дат, quota-like 503, сбор всех текстовых разделов; прояснить хранение/LLM-передачу |
| Работа России | Offset как page 0/1/2; companycode+id; корректная сборка duty/qualification/условий, даты, dedup при обновлении |
| Habr | Frontend schema contract, SSR/JSON-LD parser и identity; лимит страниц, deferred/stale; TTL detail даже при неизменном listing; anti-bot как blocked |

SuperJob больше не заблокирован отсутствием ключа. В template enabled остаётся
false: ещё нет runtime-адаптера и согласованной политики использования данных.

## Общие пробелы реализации

1. **Другие источники и общие контракты.** Остальные адаптеры, multi-source identity,
   transport/config и версионированный общий normalizer ещё не реализованы.
2. **Систематизация условий.** HH сохраняет исходные salary/gross/experience/null,
   но общая типизация периода зарплаты, grade/technology/unknown/conflict впереди.
3. **Очередь и обновления.** HH использует один процесс/flock, не lease workers.
   TTL проверяется при повторном discovery; нет полного source reconciliation,
   auto-expiry и retention/purge. Старые run читают текущие версии БД.
4. **Экспорт.** HH JSON работает из одного согласованного read snapshot, но CSV,
   неизменяемый version set по run, stable consumer cursor и retention ещё впереди.
   HH snapshot v1 отделён от будущего общего VacancySnapshot контракта.
5. **Качество охвата.** HH-проверка сотен карточек пройдена; широкие query families, ручной контроль
   потерь рынка и длительная multi-source нагрузка ещё впереди. Анализ резюме/LangGraph отложен.

## Порядок работ

HH adapter + offline fixtures + SQLite resume реализованы и проверены.
Далее — Trudvsem, Habr и SuperJob с source-specific fixtures и общий слой качества. Параллельную
обработку сотен вакансий включать после проверки общей квоты и восстановления.
Приёмка — по [acceptance.md](acceptance.md); последовательность — [roadmap.md](roadmap.md).
Реализованный HH-срез и оставшийся общий MVP перечислены отдельно выше.
