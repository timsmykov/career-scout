# Четыре источника: подключение и эксплуатация

Адаптеры находятся в [adapters.py](../src/career_scout/adapters.py), общий bounded HTTP —
[transport.py](../src/career_scout/transport.py), маппинг условий и полного текста —
[source_normalization.py](../src/career_scout/source_normalization.py). HH сохраняет
прежний transport/auth. Все источники используют одну SQLite, отдельные source identities,
сохранённые курсоры, фильтры и export. LangGraph, агенты и расписание не подключены.

## Текущий статус

| Источник | Поиск и полная карточка | Постоянный сбор |
|---|---|---|
| HH | Официальный API, проверен | Включён, прежняя база сохранена |
| Работа России | Официальный REST v1, проверен | Включён |
| Habr Career | Frontend JSON + HTML/SSR/JSON-LD, проверены | Включён; контракт frontend неофициальный |
| SuperJob | Официальный API с собственным app key, проверен | Включён; разрешения на хранение подтверждены владельцем |

Постоянное сохранение включено для всех четырёх источников. 11.11.2026 владелец
прямо подтвердил: «У меня есть все разрешения на хранение вакансий».
На этом основании в рабочем TOML установлен `sources.superjob.storage_allowed=true`.
Это подтверждение владельца для данного проекта; новых прав у API-приложения
программно не запрашивали. Анализ резюме и передача данных моделям остаются отложенными.

`probe` по-прежнему проверяет поиск/detail в памяти без открытия БД и записи текстов.
Если в отдельной конфигурации явно выставить storage_allowed=false, collect сохранит
blocked/source_storage_policy_unconfirmed без чтения вакансий. Старые frozen runs
с false не меняются от редактирования TOML; новый collect фиксирует актуальное значение.

## Команды

Основной путь: `initial --source all --progress`, затем ручной `new --source all`.
Один campaign_id связывает все четыре runs; `resume CAMPAIGN_ID` продолжает весь
проход. Initial ограничен14днями по публикации; квот на число карточек нет. Описания известных
ID не обновляются. [Полный контракт и команды](development.md#основной-путь-общий-проход-initialnew).
Команды collect ниже — прежний низкоуровневый путь для ограниченных проверок.

Из корня проекта:

```sh
# Без сети, credentials и открытия БД. По умолчанию сохранена совместимость: source=hh.
docker compose run --rm collector plan --source all
# Bounded read-only search+detail, без записи вакансий; SJ доступен в этом режиме.
docker compose run --rm collector probe --source all --query аналитик --requests 3 --per-page 2
# По отдельности:
docker compose run --rm collector collect --source trudvsem --requests 30 --details 20
docker compose run --rm collector collect --source habr --requests 30 --details 20
# Общий проход, каждый источник получает отдельный run и бюджет:
docker compose run --rm collector collect --source all --requests 30 --details 20
# RUN_ID берётся из отчёта конкретного источника:
docker compose run --rm collector resume RUN_ID --requests 30 --details 20
docker compose run --rm collector status RUN_ID
docker compose run --rm collector export RUN_ID --output /data/exports/source-selected.json
```

Бюджет `--requests/--details` задаётся **на источник**, не на весь batch. В `--source all`
идут включённые источники в порядке HH → Trudvsem → Habr → SuperJob. Четыре источника
при requests=30 могут суммарно использовать до120 попыток. Ответ содержит `runs`
с отдельными статусами/ошибками и `budget_scope=per_source`. Ошибка/403/429 одного
источника не отменяет остальные. Если хотя бы один run не complete, batch имеет
partial/exit2. Для продолжения использовать выданные run IDs, а не новый collect.

`resume` выбирает адаптер из сохранённого run.source и не требует текущего TOML.
Нативные параметры, география и бюджет сохраняются в плане. Опциональные CLI budgets
меняют только текущий invocation. `refilter` по-прежнему отдельная offline-команда.

## Контракты пагинации и поиска

| Источник | Курсор / окно | Защита от потерь |
|---|---|---|
| HH | Page0; publication_time, cap2000 | Прежние временные split с общей границей, identity dedup |
| Trudvsem | offset0/1/2 — номер страницы; modifiedFrom/modifiedTo | Body status200, meta.total/limit, companycode+vacancyid; это окно изменения, а не первичной публикации |
| Habr | Native page1; meta.currentPage/perPage/totalPages | Общий курсор0 переводится в native+1; уменьшение доступных страниц против total помечается truncated |
| SuperJob | Page0, more; date_published_from/to, cap500 | Split по датам; шаг=min(requested,40); добавленные promo rows сохраняются и дедуплицируются, их число не меняет шаг |

Habr не предоставляет подтверждённого параметра диапазона дат. План честно содержит
`window_field=all_visible`: читаются все видимые страницы запроса, `days` не выдаётся
за серверный фильтр этого источника. Нет переноса HH Boolean syntax в другие API:
16focused+broad запросов HH, по12 базовых запросов остальных площадок. Ручной `--query`
передаётся точно в нативный text/q/keyword.

Размер страницы фиксируется в `query_pagination` после первого ответа.
Для медленного поиска Trudvsem transport/5xx может создать новый query того же
диапазона с меньшим limit:20→10→5→2→1, всегда начиная с page0. Старые cursor и
discoveries сохраняются, известные full не перечитываются. Это явный pagination
replan, а не изменение шага у продолжаемого query; ошибка при limit1 остаётся partial. Следующий
запрос использует этот размер; неожиданная смена даёт pagination_size_changed и не
продвигает cursor. У SuperJob размер шага отделён от количества promo карточек.
Нет произвольного ограничения Habr20 страниц из донора; остановка по бюджету сохраняет
остаток. Меняющаяся выдача всё ещё может смещать страницы; полный охват рынка не обещан.

HH `search.area_ids`, Trudvsem `sources.trudvsem.region_code`, Habr `city_id` и
SuperJob `town_id` — разные справочники. Идентификаторы HH не отправляются остальным.
Habr имеет интервал3сек в TOML; это наша щадящая настройка, не официальная квота.
SuperJob503 считается quota-like и сохраняет cooldown без автоматических повторов.
Обходов блокировок, смен IP/аккаунта/браузера нет. Ошибки не содержат headers/key/body.

## Полный текст и условия

- Trudvsem: duty + requirements + requirement.qualification + term/занятость/график.
  Составной source_id — канонический JSON `[companycode,id]`; detail проверяет оба.
- Habr: полный SSR vacancy.description; JobPosting подтверждает ID/title/company и
  сохраняется отдельно. Зарплата/qualification/remote берутся из текущей detail-карточки,
  не из устаревшего listing. Отсутствие SSR/JobPosting или расхождение identity — error,
  карточка остаётся недочитанной. Исключение: явная закрытая карточка с совпавшими
  SSR ID/href, title, archived=true и удалёнными текстом/JobPosting — unavailable,
  без фиктивного snapshot. JSON-LD не дублирует уже полный SSR-текст.
- SuperJob: vacancyRichText и все work/candidat/compensation разделы, которых нет в rich.
  `agreement=true` и нулевые bounds не превращаются в зарплату0. Опыт `от3лет` хранится
  как minimum3 без выдуманного диапазона3–6. Постоянное сохранение разрешено владельцем и включено в рабочей конфигурации.

Общий формат вакансии/export остаётся v2/common-1. Новый source normalizer имеет
собственную версию `trudvsem-1`, `habr-1`, `superjob-1`. Исходные HH-v1 snapshots не
пересоздаются. Суммы/currency сохраняются без выдуманного gross/net/периода; источник
может иметь точный минимум опыта без категории/верхней границы. Неизвестное сохраняется,
зарплата не фильтрует. Движок `rules-3` дополнительно понимает minimum experience и
собственный Habr qualification; конфликт с названием остаётся conflict.

## Хранение и проверка

SQLite schema4 добавляла query_pagination; schema5 добавляет campaign/queue/acquisitions
без переписывания прежних строк. Перед миграцией создаётся backup0600
под тем же flock; все прежние таблицы остаются без изменений. V1/v2/v3 → v4 проходят
в одной транзакции. Refilter после миграции добавляет новую политику/решения, сохраняя
старые; исходные карточки, snapshots и поисковые состояния не переписываются.

[tests/test_sources.py](../tests/test_sources.py) проверяет нативные курсоры, restart,
75полных карточек каждого нового source, отсутствие повторного detail, caps/окна,
полные разделы, неизвестную зарплату, ошибки и независимость источников.
[Серверный отчёт](references/multi-source-live-20261111.json) содержит только метаданные:
миграция рабочей базы, bounded real сбор/продолжение и проверка сохранности старых HH.
Тексты/БД/ключи остаются вне Git. Эти проверки не равны полной приёмке покрытия рынка,
retention/reconciliation или длительной нагрузке. Проверка включения SuperJob:
[superjob-storage-live-20261111.json](references/superjob-storage-live-20261111.json).
