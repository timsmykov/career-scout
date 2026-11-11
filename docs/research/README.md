# Исследование аналогов

Проверка 11.11.2026: 28 GitHub repositories по HEAD/metadata/root, выборочное чтение
ключевых модулей, 113 source/doc snapshots для сверки. Полного аналога нашего
four-source + sequential first-PASS + LangGraph процесса в выборке не обнаружено.

Наиболее полезны radar (collectors), Job Fit Copilot (full CV / multi-resume),
NextMove (application shell), hh-ai-agent (structured output / errors).
См. ../reuse.md и machine-readable repositories.json.

Вывод не означает, что аналоги не могут существовать. Чужой runtime/тесты не запускались.
README/звёзды/свежий push не доказательство эксплуатационной зрелости.
Часть похожих n8n проектов не публикует текущий workflow. Один LangGraph demo
использует mock search. Ошибка модели не должна становиться reject.
Нельзя называть public source с Proprietary/Noncommercial/без лицензии готовой
свободной основой для копирования.

В репозиторий переносится очищенный реестр исследования, без сторонних raw
исходников, приватных путей сервера и hardcoded credentials.

[Полный отчёт](full-report.md), [реестр файлов](evidence.json), [направления поиска](search-log.json).
