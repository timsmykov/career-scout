# SuperJob

Runtime реализован в adapters.py/source_normalization.py; [общий запуск и точный статус](../multi-source.md).


Официальные страницы:
https://api.superjob.ru/
https://api.superjob.ru/rules/
https://api.superjob.ru/doc/

Поиск: GET https://api.superjob.ru/2.0/vacancies/
Заголовок X-Api-App-Id: собственный ключ приложения из secret store.
Параметры keyword, page (0), count; objects/total/more проверяются по reference.
Поля текста: vacancyRichText либо обязанности/требования/условия.
Не сохранять только title/company/salary: это недостаточный input модели.

В доступной legacy справке: 120 запросов/мин/IP, до 500 сущностей в выдаче.
Это документированные значения legacy, не подтверждённые лимиты нашего ещё
неполученного доступа. 503 может означать превышение квоты.
Фактический count/page_size проверяется; данные автора radar про ~40/page не гарантия.
При поддержке date_published_from/to большие выборки разбивать по датам.

Для своей установки зарегистрируйте приложение, задайте SUPERJOB_APP_KEY в личном
.env и проверьте ограниченный search/detail через probe. Публичный репозиторий
не включает app ID/ключ автора и не наследует разрешения его учётной записи.

В стандартном configs/search.toml storage_allowed=false. Для разрешённого вам
хранения включите его явно в своей копии TOML либо source_options плана. Probe
остаётся режимом без сохранения. Исторический [протокол](../references/superjob-storage-live-20261111.json)
показывает проверку конкретной установки, не передаёт её права новому пользователю.
