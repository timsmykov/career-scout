# HeadHunter

Официальные источники:
https://api.hh.ru/openapi/specification/public
https://api.hh.ru/openapi/redoc
https://github.com/hhru/api

Методы MVP: GET /vacancies, GET /vacancies/{vacancy_id}.
OAuth app token — по актуальному application authorization; не путать app token
с applicant/employer полномочиями. В предыдущей проверке /me is_application=true,
search/detail HTTP 200. Клиентские секреты не включены в репозиторий.

Поиск: text, page (с 0), per_page и поддерживаемые словарные параметры из OpenAPI.
found — счётчик выдачи, не число фактически сохранённых карточек.
Документированное окно выдачи до 2000 элементов надо учитывать; не обходить его
простым увеличением page. Предел per_page 100 проверять по зафиксированной версии.
Для больших выборок date_from/date_to окна с перекрытием и exact dedup.

Snippet не заменяет description полной карточки. HTML description преобразовать
в текст; сохранять source identity, salary gross/net, experience category и даты.
Запретить unknown->0 years и null salary->0 salary.

Лимит req/s не зафиксирован как универсальная гарантированная квота.
Работать с текущим договором/ответами 429; 1s interval в примере — наша политика.
401/403/captcha не являются сигналом сменить IP или браузер.
Не реализовывать отклики/resume update под видом расширения transport.
