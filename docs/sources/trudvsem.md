# Работа России / Trudvsem

Runtime реализован в adapters.py/source_normalization.py; [общий запуск и точный статус](../multi-source.md).


Официальный контракт:
https://opendata.trudvsem.ru/api/v1/vacancies/application.wadl
https://trudvsem.ru/opendata/api

REST base: https://opendata.trudvsem.ru/api/v1/vacancies
Search GET с text/limit/offset и поддерживаемыми WADL фильтрами.
offset здесь номер страницы: 0,1,2; не количество пропущенных строк.
В предыдущем чтении WADL default limit=100; не выдавать за гарантию всех максимумов.
Detail и состав identity проверить по WADL: companycode и id могут нужны совместно.

Полный смысл складывается из duty, requirement.qualification, employment/schedule
и прочих условий. Пустая qualification не означает, что duty неполный, но отсутствие
ключевого текстового содержания — missing. Оценку полноты определять контрактом,
не произвольной длиной строки.

HTTP 200 не достаточен: проверять body status, results, meta и критичные типы.
Пустая results может приходить без vacancies. Сохранять source ID, точность даты,
отдельно creation/date_modify/fetched.

Проверены search/detail HTTP 200 в предыдущем ограниченном прогоне 11.11.2026.
Универсальная квота запросов не подтверждена; interval=1s — проектная политика.
Если выдача усечена, разделять запрос только поддерживаемыми фильтрами и фиксировать
остаток. XSD массовой XML выгрузки не подменяет контракт REST.
