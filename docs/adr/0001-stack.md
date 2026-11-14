# ADR 0001: Python + LangGraph, отдельный кодовый сборщик

Статус: первоначальное решение bootstrap; ADR0004 сохраняет автономность коллектора.
12.11.2026 владелец разрешил параллельную разработку анализатора на LangGraph.
Дата: 2026-11-11.

Python 3.12, httpx, Pydantic, LangGraph, SQLite WAL и persistent SQLite checkpointer.
Вызовы оценщиков идут через установленный Codex CLI и существующий ChatGPT login.
Владелец выбрал gpt-6-luna/max через подписку, не отдельный model API.
LangChain OpenAI adapter остался bootstrap-зависимостью и не используется этим путём.

Причины: детерминированные API операции и удобная типизация; фиксированный граф
с состоянием для четырёх оценщиков; простая локальная эксплуатация.
OpenBot необязателен для ядра. CAMEL и автономный Supervisor не нужны этому маршруту.

PostgreSQL/брокер/UI/векторная БД отложены до измеренного требования.
Источники: https://docs.langchain.com/oss/python/langgraph/workflows-agents ,
https://docs.langchain.com/oss/python/langgraph/persistence .

Новый routed mode12.11 использует обычный Python-координатор четырёх профильных
очередей и SQLite ledger; LangGraph остаётся только в legacy first-PASS. Категоризация
идёт через native Decisions API; оценщики остаются на Codex subscription.
