# Эксплуатация и восстановление

Этот runbook относится к новому `career-scout`. Прежние команды коллектора
описаны в [development](development.md), контейнер и volume — в [Docker](docker.md).
Рабочая установка личная: нет daemon, scheduler и общего пользовательского сервера.

## Где находятся данные

Используйте явный `--workspace private/me` во всех связанных командах.
По умолчанию выбирается XDG-каталог или `~/.local/share/career-scout`.
[Структура SQLite и lock-файлов](architecture.md).

Env-файл задаётся ссылкой через --env. Ключи не копируются в контекст/план и вывод
CLI. HH token cache находится в workspace/auth. Codex/Claude используют собственный
login на хосте; API-оценщик — переменную окружения или приватный env-файл.
Подписочная авторизация не является частью приложения. [Подключения](model-providers.md).

Docker Compose использует external volume `career-scout-data`, путь
`/data/ledger.sqlite3`, read-only `.env` и search.toml. Это прежний collector CLI,
а не автоматически тот же collector.sqlite3, что внутри личного agent-workspace.
Всегда сверяйте путь и run_id; не объединяйте разные базы вручную.

## Продолжение коллектора

```sh
career-scout --workspace private/me run status COLLECTION_RUN_ID
career-scout --workspace private/me run resume COLLECTION_RUN_ID --request-id recover-collection-1
```

Если исчерпан общий HTTP-бюджет, после проверки отчёта добавьте конечное число
через additional-requests. Resume сохраняет исходное окно/план и checkpoints;
новый run start --mode new нужен для следующего свежего окна.

При run_busy не запускайте дубликат и не удаляйте lock-файлы ради обхода.
Проверьте владельца процесса. При завершении процесса ОС снимает файловый lock.
Сетевой ответ, потерянный до commit, иногда потребуется перечитать; сохранённая
full-карточка не должна скачиваться заново. Exactly-once внешнего HTTP нет.

401/403/429 не исправляются новым пулом, сменой IP или учётной записи. Смотрите
ошибку источника, retry_at и своё подключение. Неполный проход не является нулевым
рынком. [Таблица диагностики](troubleshooting.md).

## Продолжение анализа

```sh
career-scout --workspace private/me evaluation status EVALUATION_RUN_ID --db DATABASE
career-scout --workspace private/me evaluation resume EVALUATION_RUN_ID --db DATABASE \
  --additional-calls 2 --request-id recover-evaluation-1
```

Не добавляйте бюджет без проверки execution_error. Decisions имеет отдельный
additional-decisions. Retry-errors открывает конечную попытку известной технической
ошибки; batch_outcome_unknown и model_outcome_unknown требуют сверки попытки и
истории провайдера. Native-коды с outcome_unknown имеют ту же границу. Не стирайте журнал
и не меняйте хэши ради повторного вызова. Ошибка модели не становится REJECT.

Внешние вызовы выключены в примере подключения. Их явное включение через
--enable-calls относится к одному подготовленному run и сохраняет его входы/лимиты.
Следующий prepare продвигается к следующему остатку; он не заменяет resume.

## Повтор команды и квитанция

```sh
career-scout --workspace private/me operation show REQUEST_ID
```

Тот же request-id с теми же аргументами возвращает исходную квитанцию; для свежего
статуса есть status. Если receipt не завершён, операция могла успеть выполнить
действия. Сначала сверяйте состояние, затем продолжайте прежний run новым ID.
Конфликт event operation_id проверяется для всего импорта истории до первой записи;
при прерывании I/O независимые ID событий помогают сверить уже выполненную часть.

## Резервная копия и перенос

Пересоздание контейнера сохраняет volume, но не защищает от потери сервера.
Политика автоматических backup не включена. До собственного переноса остановите
исполнители и используйте согласованную копию SQLite через backup API либо
копирование полного остановленного workspace с его файлами SQLite/WAL.
Не копируйте только основной sqlite-файл работающего writer.

На новом хосте настройте Python, выбранный API или подписочный CLI, нужный
Decisions-коннектор и свои credentials. Не публикуйте backup и не переносите
подписочный auth в Docker image.
Изменившиеся абсолютные пути и native workspace/root нужно перенастроить явно.
Приложение не предоставляет автоматическую миграцию всего окружения между хостами.

## Что считать успешным результатом

Проверяйте completed/partial отдельно для сбора и анализа, остаток pending/error,
число сохранённых карточек, бюджеты и соответствующую версию контекста. В подборке
только PASS, UNCERTAIN включается отдельно. Без разметки quality.measured=false.
Актуальность и история могут уменьшать подборку со временем без повторной модели.
