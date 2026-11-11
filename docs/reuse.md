# Откуда брать реализацию сборщика

## Выбор

Для нашего API-агрегатора лучший референс — ru-it-jobs-radar, но он не готовый
drop-in пакет. В нём уже разобраны три выбранных источника и ошибки полноты.
HH дописываем по официальному OpenAPI. Общий исполнитель небольшой: source
adapters → versioned ledger → очередь. Ни LLM-сборщик, ни весь аналитический стек не нужны.

## Зафиксированный донор

alekseevpavel04/ru-it-jobs-radar
commit: 035bd435763062543eba884a5cd5298390b20675
license: MIT, Copyright (c) 2026 Pavel Alekseev.

| Файл относительно src/radar/ | Применение | Изменение для нас |
|---|---|---|
| collect/http.py | finite retry, timeout, source pause | HTTP-date Retry-After, общий бюджет, typed errors, общие лимиты |
| collect/contracts.py | проверка критичных полей, extra allow | выбрать минимум обязательных полей; unknown не терять |
| collect/envelope.py | source ID, timestamps, content hash | наши completeness/version/retention, без Parquet dependency |
| collect/trudvsem.py | offset как page, run stats | наш query plan/cursors/SQLite |
| collect/superjob.py | date windows, cap, dedup | собственный ключ, действующие условия, source cooldown |
| collect/habr.py | список/detail отдельно, отложенные карточки | stale явно; TTL refresh без смены listing hash |
| tests/collect/test_habr.py | cap/deferred/blocked сценарии | не считать _SUCCESS полной готовностью текста |
| tests/collect/test_superjob.py | окна, дубли и типы данных | наши fixtures/контракт и source policy |

Точные пути:
https://github.com/alekseevpavel04/ru-it-jobs-radar/tree/035bd435763062543eba884a5cd5298390b20675/src/radar/collect

Сейчас эти файлы не включены в наш source tree. Перед переносом: проверить
актуальный upstream, сохранить MIT/license, отметить diff и тесты. Проектный
NOTICE сейчас описывает референс, не утверждает выполненное заимствование.

## Что не переносить

ClickHouse, dbt, Kafka/Redpanda, Airflow, Kubernetes, fine-tuned extractor,
зарплатные модели и SQL-agent. Они решают другую задачу и увеличат bootstrap.

## Другие референсы

- iurii-izman/career-signal-hh: official HH API, бюджет, partial results.
  commit f8cfc70b846b5050700556fad84c049959d37b2b.
  Лицензия не найдена в проверенных root/pyproject; только архитектурный ориентир.
- zhurik77/jobfitcopilot: full resume и several resumes; MIT.
  Не переносить ranking всех вместо первого PASS.
- fikstt2/hh-ai-agent: structured validation и analysis_failed; MIT.
  Бинарный suitable заменить нашим трёхклассовым предметным ответом.
- speedyapply/JobSpy: MIT, общий интерфейс, но выбранных российских источников нет.

Готовый форк NextMove рационален при необходимости его UI. Сейчас CLI и отдельный
LangGraph-сервис проще соответствуют нашей задаче.
