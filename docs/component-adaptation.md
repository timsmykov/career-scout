# Адаптация ru-it-jobs-radar

Дата анализа: 11.11.2026. Актуализация: интерфейсы transport/native adapters/planner/SQLite реализованы
отдельно для Career Scout; таблица ниже сохраняет исходную карту референса.
Исполняемые файлы донора не скопированы. [Фактические компоненты](multi-source.md).

## Основание

Репозиторий: https://github.com/alekseevpavel04/ru-it-jobs-radar
Базовый commit: 035bd435763062543eba884a5cd5298390b20675.
Текущий HEAD при повторной проверке: ec2feff2b659edad9771b07b15337bdde7889cd5.
Проверены также текущие blobs девяти collect-модулей. Перенос фиксировать по
конкретному commit и diff; обновление upstream не внедряется автоматически.
MIT, Copyright (c) 2026 Pavel Alekseev. Сохранять лицензию при адаптации.
Чужие исполняемые файлы в наш пакет пока не включены.

## Что брать и куда

Пути upstream ниже относительно src/radar/. Целевые пути — план.

| Upstream / механизм | Наше место | Что сохранить | Что адаптировать | Приёмка |
|---|---|---|---|---|
| collect/http.py: Fetcher | collectors/transport.py, budget.py | HTTPX, конечные повторы, паузы, счётчики | Передать config/clock/sleeper; общий source budget; HTTP-date Retry-After; typed errors; не sleep внутри async worker | Fake clock: 429 останавливает весь источник, retries учитываются в бюджете |
| collect/contracts.py | collectors/contracts/{habr,trudvsem,superjob}.py | Минимальные критичные типы, разрешение новых полей | Отделить envelope ошибки от поломанной строки; неизвестные необязательные атрибуты сохранить; HH написать отдельно | Новое поле допускается, поломанный ID/список не становится пустой успешной выдачей |
| collect/envelope.py | domain/vacancy.py, normalization/hashing.py, time.py | Source identity, UTC, хеш, отличие дат | Наши detail_status, typed salary, точность дат, версия нормализатора; provenance вне смыслового hash | Новый запрос/время чтения не меняют content hash; изменение условия меняет |
| collect/habr.py: html_to_text | normalization/html.py | Абзацы, списки, переносы br | Вынести общий механизм из Habr: SuperJob/HH не импортируют другой адаптер; определить parser dependency | Отсутствие потери разделов, удаление script/style, fixtures malformed HTML |
| collect/trudvsem.py: iter_query | collectors/adapters/trudvsem.py | offset как page, status/meta, truncation | Наши запросы вместо IT-only списка автора; companycode+id; квалификация/условия; persisted cursor и watermark | Страницы 0/1/2, duty+requirement.qualification не теряются |
| collect/trudvsem.py: watermark | collectors/progress.py | Не брать watermark из локальных часов | По каждому source/query/window, только complete, перекрытие; periodic reconciliation | Partial run не пропускает записи следующего запуска |
| collect/superjob.py: walk_window/consume | collectors/adapters/superjob.py, windows.py | Перекрывающиеся интервалы дат, cap, more, sink-dedup | Наш app key, проверенный page size, min window, finite budget, сохранение незавершённых окон; не копировать IT-title-only QUERIES | Переполненное окно делится; повтор границы не дублирует; хвост виден |
| collect/superjob.py: vacancy_text/to_record | normalization/superjob.py | Rich text и fallback work/candidat/compensation | Общая HTML-очистка, единая salary/conditions, identity до очистки персональных полей | Полное описание, null salary не превращается в 0 |
| collect/habr.py: walk/collect_list | collectors/adapters/habr.py | page 1, union проходов, counters | Наш query plan, контроль capped passes, не ограничиваться только IT-группами автора | Каждый проход имеет полноту, не теряются junior/unknown |
| collect/habr.py: fetch_card/collect | collectors/adapters/habr.py, detail_service.py | SSR/JSON-LD, ID карточки, deferred | Разделить list/detail statuses, TTL при неизменном listing, согласованность старого detail, budget/cooldown | Старая карточка после изменения списка — stale; не уходит на оценку |
| tests/collect/test_http.py, test_contracts.py, test_envelope.py | tests/collectors/, tests/domain/ | Сценарии transport/type/hash | Переписать под наши контракты/clock, убрать зависимости radar | Проверяют поведение, не буквальную копию реализации |
| tests/collect/test_trudvsem_api.py, test_habr.py, test_superjob.py | tests/collectors/ | Сценарии пагинации, cap, changed/deferred | Заменить Parquet assertions на SQLite, собственные вымышленные fixtures | Тесты проходят без сети и ключей |

Проверенные upstream каталоги:
https://github.com/alekseevpavel04/ru-it-jobs-radar/tree/035bd435763062543eba884a5cd5298390b20675/src/radar/collect
и tests/collect в том же commit.

## Что заменить

| Upstream | Почему не переносим целиком | Наша замена |
|---|---|---|
| collect/writer.py | PartitionWriter зависит от PyArrow/Parquet, дневных partitions и _SUCCESS; нужен транзакционный ledger | SQLite repositories: upsert snapshots/discoveries + query cursor в одной транзакции |
| collect/summary.py | Читает Parquet/файловые metadata, считает partition statistics | SQL-агрегаты по query/run/detail/evaluation; complete и partial явно |
| collect/lock.py | PID/file lock с psutil решает запуск одного процесса, не ownership задач | Атомарный claim/lease в SQLite; source-run ownership; проверка истечения lease |
| collect/__init__.py | setup_logging загружает radar.config и меняет глобальный stdout | Наш entrypoint: явный config/env, redaction, структурированные события |
| radar.config, radar.results | Собственные директории/параметры и артефакты автора | Наш config loader и report/export service |
| radar.stream.producer | Публикация в поток связана с чужим аналитическим стеком | Локальная work_items queue; брокер пока не требуется |
| radar.pii.* | Правила очистки могут менять company IDs/названия, нужны внешние словари | Минимизация по нашей политике; identity до очистки; модели получает только нужный текст |

Не копировать весь PII-пакет со словарём имён без необходимости. Перед переносом
проверить, не удаляет ли он деловые условия или идентификаторы. В одном из collect
модулей секции requirements/conditions и requirement.qualification имеют разные
пути: соответствие реальному payload проверить отдельной fixture, а не предполагать.

## Почему недостаточно скопировать три файла

Каждый адаптер импортирует contracts/envelope/http, writer/summary, config,
results, PII и stream producer. У SuperJob есть импорт html_to_text из Habr.
Прямое копирование втянет чужой storage и аналитические зависимости.
После адаптации в collectors не должно остаться imports radar.* или pyarrow.

Никакие подтверждения доступа и численные лимиты автора не наследуются:
используем собственные ключи и результаты нашего source acceptance.

## Что пишем сами

- HH official search/detail и lifecycle application token.
- Domain SourceQuery/SourcePage/typed cursor/SourceError и feature capabilities.
- Широкий план поиска по четырём профилям, fair budgets и контроль охвата.
- Versioned SQLite ledger, migrations, discoveries, cursor, leases, TTL и восстановление.
- Нормализация salary/grade/experience/дат с provenance и unknown/conflict.
- Доказуемые hard filters и очередь полного текста.
- Импорт четырёх актуальных резюме и версии рубрик/условий.
- Model adapter, строгий ответ, проверка цитат и first-PASS LangGraph.
- Кэш оценок по всем входным версиям, review queue и локальный export.
- Диагностика/стоимость/сроки хранения, offline/live acceptance и оценка потерь.

## Процедура переноса одного компонента

1. Зафиксировать SHA, файл и лицензию, сохранить upstream notice.
2. Выбрать минимальную функцию, выписать зависимости и поведение.
3. Создать наши модели/интерфейс, заменить radar storage/config/stream.
4. Перенести содержательные offline-сценарии, добавить наши негативные случаи.
5. Проверить эквивалентность нужного поведения и намеренные отличия.
6. Обновить NOTICE и эту таблицу: adapted/implemented, точные пути, commit переноса.
7. Только после offline checks — отдельный bounded live acceptance площадки.

Оценка: сложнее всего состояние и полнота, затем Habr и SuperJob windows.
Общий HTTP и модели — меньший перенос; автоматическое сравнение четырёх резюме
и качество отбора в доноре для нашей задачи отсутствуют.
