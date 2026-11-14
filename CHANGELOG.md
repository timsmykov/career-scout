# История изменений

## 0.1.0 alpha — Unreleased

- Clarify the current Russian-market scope in public documentation and the agent
  skill; international search requires separate sources and workflow.


- Install the bundled Career Scout Agent Skill with `skill install --agent codex`
  or `--agent claude`; JSON discovery and the installer preserve existing custom skills.
- Choose among ten evaluator API providers or the official Claude Code subscription
  transport; keys remain environment references, validation/queue budgets are shared.
- Resume stops on unknown native/API delivery in both current and legacy paths
  before increasing budgets; unclassified Codex failures no longer trigger blind retries.
- Check single-CV setup with `doctor --context`: Decisions is optional, and evaluating
  an existing catalog does not require platform credentials.


Публичная версия находится в main. Запись описывает alpha, а не стабильный1.x
или опубликованный PyPI-релиз.

### Возможности

- Можно собрать полные вакансии четырёх источников, продолжить прерванный проход
  и следующим ручным запуском добавить новые ID без повторной загрузки старых описаний.
- Личный JSON CLI принимает произвольные полные резюме, цели и пожелания;
  сохраняет версии, планы, конечные бюджеты и квитанции команд.
- Персональный analysis context-1 выбирает одно/два CV через внешний Decisions-коннектор
  и оценивает карточки Codex через подписку. Для одного CV при выключенном автоотсеве
  Decisions не нужен.
- Подборка показывает PASS, отдельно включает UNCERTAIN, группирует по резюме,
  учитывает импортированную историю и раскрывает основания через explain.
- Метрики по контрольной разметке доступны через CLI; без меток качество отмечается
  как неизмеренное. [Начать](https://github.com/timsmykov/career-scout/blob/main/docs/quickstart.md).

### Надёжность и исправления

- Исправлена повторная SQLite-транзакция при передаче collection-run в evaluation.
- Конфликты event operation_id во всём пакете истории проверяются до первой записи.
- Полный offline-сценарий установленного пакета добавлен в CI вместе с проверкой
  данных после пересоздания Docker-контейнера. [Протокол 0.1](https://github.com/timsmykov/career-scout/blob/main/docs/product-audit-0.1.md).

### Упаковка и ограничения

- MIT, Python3.12, wheel/sdist, Docker с постоянным volume и locked-зависимости.
- Новый персональный путь документирован отдельно от совместимых старых режимов.
- Для нескольких CV нужен внешний native Decisions helper; Codex не поставляется
  в image. Habr использует frontend JSON/HTML. Рыночная точность ещё не установлена.

### Публичная настраиваемая поставка

- Убраны отрасль, четыре направления и стажевые ограничения из стандартного поиска;
  без своих запросов коллектор останавливается до HTTP.
- Defaults не предоставляют разрешение хранения SuperJob от имени пользователя.
- Decisions и экспорт файлов больше не зависят от путей OpenBot или UID сервера автора.
- Добавлены проверки нейтральных настроек, произвольного senior/non-IT поиска,
  изолированного экспорта и отсутствия приватных данных в текущем Git-снимке.
- [Адаптация](docs/personalization.md), [проверка поставки](docs/public-release.md).
