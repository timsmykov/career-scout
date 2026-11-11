# ADR 0001: Python + LangGraph, отдельный кодовый сборщик

Статус: принято для bootstrap по предпочтению владельца; runtime не реализован.
Дата: 2026-11-11.

Python 3.12, httpx, Pydantic, LangGraph, SQLite WAL и persistent SQLite checkpointer.
LangChain OpenAI adapter — возможный OpenAI-compatible транспорт; его наличие
не подтверждает доступ к конкретной Luna/Sol модели.

Причины: детерминированные API операции и удобная типизация; фиксированный граф
с состоянием для четырёх оценщиков; простая локальная эксплуатация.
OpenBot необязателен для ядра. CAMEL и автономный Supervisor не нужны этому маршруту.

PostgreSQL/брокер/UI/векторная БД отложены до измеренного требования.
Источники: https://docs.langchain.com/oss/python/langgraph/workflows-agents ,
https://docs.langchain.com/oss/python/langgraph/persistence .
