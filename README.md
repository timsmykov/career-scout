# Career Scout

**От поисковой выдачи к подборке вакансий под ваши резюме.**

![Версия](https://img.shields.io/badge/version-0.1.0_alpha-d99c28)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![Лицензия](https://img.shields.io/badge/license-MIT-238636)

[Первый запуск](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/quickstart.md) · [Документация](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/README.md) ·
[Архитектура](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/architecture.md) · [Кейс](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/case-study.md) · [English](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/README.en.md)

Career Scout собирает полные вакансии из HH, Habr, SuperJob и «Работы России»,
сохраняет их в SQLite и сопоставляет с полными резюме и пожеланиями кандидата.
Результат — ссылки для самостоятельных откликов, подходящее резюме и основания
оценки. Настраивать сервис может человек или агент через JSON CLI.

**0.1.0 — личная alpha-версия для Linux/Python 3.12.** Рабочая реализация находится
в ветке `feat/evaluation-workers`; стабильный релиз 1.x не заявлен.
[Что проверено и что осталось](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/product-audit-0.1.md).

> На main пока сохранён bootstrap. Для запуска используйте ветку
> `feat/evaluation-workers`, как в команде установки ниже. Все ссылки ведут к её документации.

## Как это работает

```mermaid
flowchart LR
    C[Резюме и пожелания] --> P[Поисковый план]
    P --> S[Сбор с четырёх площадок]
    S --> D[(SQLite: полные карточки)]
    D --> F[Очередь и явные ограничения]
    C --> R[Выбор резюме]
    F --> R
    R --> E[Оценка Codex]
    E --> V[Проверка доказательств]
    V --> L[Подборка для откликов]
```

Сбор, страницы, дедупликация, бюджеты, сохранение и очередь выполняет код.
При нескольких резюме внешний Decisions-коннектор выбирает одно или два; при
одном резюме и выключенном автоотсеве классификация не нужна. Codex оценивает
каждую вакансию независимо, до 20 карточек одного резюме в батче, до 4 слотов.

## Что можно делать

| Задача | Поведение |
|---|---|
| Настроить поиск под себя | Произвольные резюме, цели, свободные пожелания и обязательные условия |
| Собрать вакансии | Полные описания, источник и ID, страницы, окно публикации, продолжение после сбоя |
| Добавить новые | Новый ручной проход перечитывает выдачу, скачивает описания только новых ID |
| Получить рекомендации | PASS отдельно от UNCERTAIN, категории по резюме, ссылки и explain |
| Сохранить прогресс | Версии контекста, квитанции request-id, кэш оценок, конечные бюджеты |
| Учесть отклики | Импорт истории скрывает уже откликнутые вакансии |
| Проверить качество | Метрики по своей контрольной разметке; без неё качество отмечено как неизмеренное |

Профессия и отрасль не зашиты в новый CLI. Отсутствие зарплаты или другого
необязательного поля само по себе не становится отказом.

## Начать

```sh
git clone --branch feat/evaluation-workers https://github.com/timsmykov/career-scout.git
cd career-scout
uv sync --locked
uv run --no-sync career-scout version
uv run --no-sync career-scout schema
```

Используйте [пошаговую инструкцию](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/quickstart.md): она проводит от одного
резюме до первой подборки с конечными HTTP- и модельными бюджетами.

- **Для сбора** нужны собственные доступы к выбранным источникам; env-файл — по
  [.env.example](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/.env.example). Права на хранение данных оформляются отдельно.
- **Для оценки** нужен установленный на хосте Codex с действующим входом через
  ChatGPT и доступной вам моделью. Ключ model API оценщикам не требуется.
- **Для нескольких резюме** нужен совместимый внешний Decisions-коннектор.
  Он пока не поставляется с пакетом. [Подключения](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/context-evaluation.md).
- **Docker** содержит коллектор и CLI; Codex и авторизация в образ не входят.
  SQLite вынесена в постоянный volume. [Docker и данные](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/docker.md).

## Четыре источника

| Источник | Транспорт | Особенность |
|---|---|---|
| HH | Официальный API | Авторизация приложения, полные карточки, разбиение больших окон выдачи |
| Habr | Frontend JSON + HTML | Нет подтверждённого публичного API поиска; интеграция зависит от frontend |
| SuperJob | Официальный API | Ключ приложения и явное разрешение постоянного хранения |
| «Работа России» | Официальный REST | Своя пагинация, география и составной идентификатор |

[Контракты площадок](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/sources/README.md). Успешный probe подтверждает доступ
в момент проверки; он не гарантирует полный охват рынка или будущую доступность.

## Проверка и ограничения

При приёмке 0.1 прошли **484 offline-теста**, сборка wheel/sdist, сохранение данных
после пересоздания контейнера и полный сценарий установленного пакета с эмуляторами
HTTP/Codex. Отдельные живые поиск и detail проверены у всех четырёх площадок.
[Полный протокол и границы выводов](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/product-audit-0.1.md).

Смысловая точность нового персонального отбора ещё требует широкой разметки.
Проверка цитат не гарантирует правильного вывода модели. Автоотсев Decisions по
вероятности по умолчанию выключен. Приложение не отправляет отклики, не правит
резюме и не запускает расписание. Сохранённые старые описания автоматически не
обновляются; перед откликом проверьте оригинал вакансии.

## Навигация

- Пользователю: [резюме и условия](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/guides/candidate.md), [сбор](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/guides/collection.md),
  [анализ и подборка](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/guides/evaluation.md), [решение проблем](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/troubleshooting.md).
- Разработчику: [CONTRIBUTING](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/CONTRIBUTING.md), [модули](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/src/career_scout/README.md),
  [CLI-контракт](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/agent-cli.md), [схемы](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/schemas/agent-context.schema.json),
  [вымышленные примеры](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/examples/README.md).
- О продукте: [кейс и решения](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/case-study.md), [аудит 0.1](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/product-audit-0.1.md),
  [история изменений](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/CHANGELOG.md), [границы продукта](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/product.md).

Прежний `python -m career_scout` и четыре профиля владельца сохранены отдельным
совместимым режимом. Их [TOML-фильтры](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/filtering.md) не являются настройками
нового персонального CLI. [Указатель всей документации](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/README.md).

## Участие и лицензия

Сообщения об ошибках и небольшие проверяемые PR приветствуются.
[Как внести вклад](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/CONTRIBUTING.md) · [Безопасное сообщение об ошибке](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/SECURITY.md).
Реальные резюме, вакансии, ключи, cookies и рабочие базы не включаются в Git.

Собственный код распространяется по [MIT](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/LICENSE). Сторонние права и использованные
референсы описаны в [NOTICE](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/NOTICE.md) и [reuse](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/reuse.md).
