# Официальные контракты и XERJ

Официальные URL, тип документа, версия/хеш и ограничения — api-sources.json.
Снимки доступны в уже настроенном shared XERJ; отдельный поисковый сервер не нужен.

| Corpus | Index |
|---|---|
| hh-public-api | xw-doc-hh-public-api-docs |
| superjob-api-docs | xw-doc-superjob-api-docs-docs |
| trudvsem-api-docs | xw-doc-trudvsem-api-docs-docs |
| habr-career-api-docs | xw-doc-habr-career-api-docs-docs |

На сервере с готовым binding:
`xerj-documents search hh-public-api "vacancies pagination application authorization"`

Для ручного DSL сначала actual mapping. Искать датированную версию и сверять
первоисточник. XERJ watch обновляет локально включённые файлы, не скачивает сайты.
Старая страница и новый OpenAPI могут одновременно возвращаться в корпусе.
Число записей индекса не доказывает полноту актуальной документации.

Ограничения: SuperJob /doc/403; Trudvsem main reference460, WADL доступен;
Habr official CRM reference не описывает frontend search.
Публичные API documents не содержат личных резюме и secrets.
Новый checkout не требует копировать всю vendor документацию в Git.

## Проверка первичного сбора

[Initial за14дней](initial-14d-live-20261112.json) — промежуточный live-аудит:
фиксированное окно, четыре источника, сохранность SQLite и проверка полных текстов.
Поле `status: partial` означает, что обход поискового плана ещё продолжается;
проверенные снимки и свежая выгрузка не доказывают завершения всех запросов.
