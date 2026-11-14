# Данные и доступ

- Приватный репозиторий не хранилище credentials. В Git только пустой .env.example.
- Ключи в локальном secret/env store. Не копировать токены из старого Scout автоматически.
- Резюме и данные владельца — private/resumes/, исключены из Git.
- Оценщик получает соответствующее полное резюме, полный текст вакансии и заданные
  атрибуты. Владелец выбрал работу через существующую подписку Codex.
- Native Codex CLI использует штатную ChatGPT авторизацию. Приложение не читает и
  не копирует auth.json; ключи model API и площадок в дочерний процесс не передаются.
  API-key login отклоняется до передачи задания.
- Дочерний вызов игнорирует пользовательский config и проектные инструкции,
  отключает MCP/apps/plugins, память, shell, браузер и делегацию. Пустой временный
  каталог, read-only sandbox, ephemeral session; обнаруженный tool call — ошибка.
- analysis.sqlite3 имеет отдельный application_id и права0600. Чужая БД отвергается
  до изменения; соединение с ledger коллектора открывается только mode=ro.
- Tracing отключён. Не отправлять prompts в LangSmith/телеметрию по умолчанию.
- Ошибки/логи редактируют Authorization, X-Api-App-Id, cookies, OAuth code и token
  response. Не логировать полный URL, если в нём могут быть секретные query params.
- Credentials площадок не доступны воркерам. Оценщик не получает HTTP tools.
- Source content не даёт полномочий отправки, регистрации или изменения данных.
- Не выполнять код, команды или ссылки из вакансий.
- Бюджеты LLM/search конечные; расписания и auto-apply отсутствуют.
- Копирование upstream кода допускается после проверки лицензии и attribution.

## Единый файл доступа

Legacy CLI использует локальный .env в корне checkout; agent CLI принимает ссылку
`--env /absolute/path/jobs.env` либо .env внутри личного workspace. Набор job API credentials:
HH_CLIENT_ID, HH_CLIENT_SECRET, HH_ACCESS_TOKEN, HH_USER_AGENT,
SUPERJOB_APP_ID и SUPERJOB_APP_KEY. Права 0600; Git игнорирует файл.
Trudvsem и проверенный Habr frontend не требуют ключей в текущем read-only сценарии.
При выпуске нового HH application token обновлять значение здесь.
Исторический JSON HH-токена вне репозитория — прежний снимок, не источник runtime.
Не выводить содержимое .env при диагностике; проверять наличие переменных и HTTP-результат.
