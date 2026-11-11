"""Adapters expose zero-based page cursors; source pagination remains explicit."""

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class Page(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[dict]
    found: StrictInt = Field(ge=0)
    pages: StrictInt = Field(ge=0)
    page: StrictInt = Field(ge=0)
    per_page: StrictInt = Field(ge=1, le=100)
    has_more: StrictBool | None = None
    window_cap: StrictInt | None = 2000
    truncated: StrictBool = False


class SourceClient(Protocol):
    source: str
    window_cap: int | None
    supports_date_split: bool
    on_request: Callable[[], None]

    def clock(self) -> float: ...

    def search(
        self,
        query: str,
        start: str,
        end: str,
        page: int,
        per_page: int,
        *,
        area_ids: list[str] | None = None,
    ) -> Page: ...

    def detail(self, vacancy_id: str) -> dict: ...
