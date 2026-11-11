# Источники

Текущий runtime и ограничения: [multi-source.md](../multi-source.md).


Наблюдения за доступом обновлены ограниченной live-проверкой 11.11.2026.
Они не означают, что новый репозиторий уже умеет делать запросы.

| Source | Транспорт | Подтверждено | Остаток |
|---|---|---|---|
| [hh](hh.md) | официальный API | app token /me, search, full detail HTTP 200 | реализовать свой адаптер |
| [trudvsem](trudvsem.md) | официальный REST | search/detail HTTP 200 | контракт полей, пагинация, broad plan |
| [habr](habr.md) | frontend JSON + HTML | список и полный HTML detail прочитаны | нет официального опубликованного search-контракта |
| [superjob](superjob.md) | официальный API | app 5273; search/detail HTTP 200 | адаптер/окна реализованы, постоянное сохранение включено |

Registry не должен делать auto-enable по факту наличия URL.
Все источники disabled в шаблоне. Значения лимитов — explicit source config.

[Результат живой проверки и разбор готовности](../collector-readiness.md).
