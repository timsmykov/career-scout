# Источники

Наблюдения за доступом — 11.11.2026 в предыдущей проверке этого проекта.
Они не означают, что новый репозиторий уже умеет делать запросы.

| Source | Транспорт | Подтверждено | Остаток |
|---|---|---|---|
| [hh](hh.md) | официальный API | app token /me, search, full detail HTTP 200 | реализовать свой адаптер |
| [trudvsem](trudvsem.md) | официальный REST | search/detail HTTP 200 | контракт полей, пагинация, broad plan |
| [habr](habr.md) | frontend JSON + HTML | список и полный HTML detail прочитаны | нет официального опубликованного search-контракта |
| [superjob](superjob.md) | официальный API | доступна applicant reference; app draft | ключ/реальный сайт/условия хранения |

Registry не должен делать auto-enable по факту наличия URL.
Все источники disabled в шаблоне. Значения лимитов — explicit source config.
