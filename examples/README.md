# Вымышленные примеры

Здесь нет резюме владельца или реальных вакансий. Поля, ссылки и идентификаторы
служат проверке контрактов; статические 64-символьные хэши примеров иллюстративны.
Runtime самостоятельно вычисляет ключи по реальным входам и версиям.

## Новый персональный CLI

| Пример | Использование |
|---|---|
| [agent-context](agent-context.synthetic.json) | Полное CV, цели и пожелания; замените своим приватным документом |
| [agent-plan](agent-plan.synthetic.json) | Запросы к выбранному источнику, окно публикации |
| [agent-limits](agent-limits.synthetic.json) | Общий HTTP-бюджет и порция |
| [agent-connection](agent-connection.synthetic.json) | Модель/слоты/батч; внешние вызовы выключены |
| [context-result](context-result.synthetic.json) | Сохранённый результат v4/context-1, не команда запуска модели |

[Первый запуск](../docs/quickstart.md) показывает порядок команд. Не отправляйте
в модель вымышленный пример как свой подтверждённый опыт.

## Общие и прежние контракты

- [vacancy-v2](vacancy-v2.synthetic.json) — текущая общая карточка.
- [vacancy](vacancy.synthetic.json) — ранний формат snapshot.
- [worker-input](worker-input.synthetic.json) ↔ [evaluation-pass](evaluation-pass.synthetic.json).
- [borderline input](worker-input-borderline.synthetic.json) ↔ [borderline result](evaluation-borderline.synthetic.json).
- [reject input](worker-input-reject.synthetic.json) ↔ [reject result](evaluation-reject.synthetic.json).
- [application input](worker-input-applications.synthetic.json) ↔ [application result](application-pass.synthetic.json).
- [collection report](collection-report.synthetic.json) — partial, а не результат живого API.

Для REJECT показан другой логический профиль, а не отрицание опыта соседнего CV.
Скрипт check_repository проверяет16пар пример/схема и отдельные цитаты. Runtime
дополнительно проверяет ключи, источники и доказательства; ни одна из этих проверок
не доказывает смысловую правильность вывода. [Приёмка](../docs/product-audit-0.1.md).
