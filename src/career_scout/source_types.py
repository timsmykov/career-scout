"""Adapters expose zero-based page cursors; source pagination remains explicit."""

from collections.abc import Callable
from functools import wraps
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from career_scout.common import SourceError


def contract_boundary(operation):
    """Expose safe drift errors when a source changes a required shape/type."""

    def decorate(method):
        @wraps(method)
        def wrapped(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            except SourceError:
                raise
            except (ValueError, TypeError, KeyError, AttributeError):
                raise SourceError(operation + "_schema_drift") from None

        return wrapped

    return decorate


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

    @model_validator(mode="after")
    def valid_identities(self):
        if any(not isinstance(i.get("id"), str) or not i["id"].strip() for i in self.items):
            raise ValueError("search_item_identity_missing")
        return self


class SourceClient(Protocol):
    source: str
    window_cap: int | None
    supports_date_split: bool
    on_request: Callable[[], None]

    def clock(self) -> float: ...

    def search(
        self,
        query: str,
        start: str | None,
        end: str | None,
        page: int,
        per_page: int,
        *,
        area_ids: list[str] | None = None,
    ) -> Page: ...

    def detail(self, vacancy_id: str) -> dict: ...
