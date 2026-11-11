"""Validated, network-free search plans. Every run freezes its settings."""

import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from career_scout.common import SourceError, digest
from career_scout.models import Model, Source

ENGINE_VERSION = "rules-3"


class FilterPolicy(Model):
    allowed_grades: list[Literal["intern", "junior", "middle", "senior", "lead", "head"]] = Field(
        min_length=1
    )
    allowed_experience: list[
        Literal["no_experience", "one_to_three", "three_to_six", "over_six"]
    ] = Field(min_length=1)
    max_required_years: float = Field(default=3, ge=0, le=50, allow_inf_nan=False)
    keep_unknown: Literal[True] = True
    salary_optional: Literal[True] = True
    domain_terms: list[str] = Field(min_length=1)
    outside_role_names: list[str] = Field(default_factory=list)
    outside_industry_names: list[str] = Field(default_factory=list)

    @field_validator("domain_terms", "outside_role_names", "outside_industry_names")
    @classmethod
    def terms(cls, values):
        if any(not x.strip() or len(x) > 200 for x in values):
            raise ValueError("invalid_filter_term")
        return list(dict.fromkeys(values))

    def key(self):
        return digest({"engine": ENGINE_VERSION, "policy": self.model_dump(mode="json")})


class SearchSettings(Model):
    days: int = Field(default=7, gt=0, le=3650)
    per_page: int = Field(default=100, ge=1, le=100)
    refresh_hours: float = Field(default=24, ge=0, le=87600, allow_inf_nan=False)
    area_ids: list[str] = Field(default_factory=list)
    focused_queries: bool = True
    focus_terms: list[str] = Field(min_length=1)

    @field_validator("focus_terms")
    @classmethod
    def terms(cls, values):
        if any(not x.strip() or len(x) > 200 for x in values):
            raise ValueError("invalid_focus_term")
        return list(dict.fromkeys(values))

    @field_validator("area_ids")
    @classmethod
    def areas(cls, values):
        if any(not x.isascii() or not x.isdigit() for x in values):
            raise ValueError("invalid_area_id")
        return list(dict.fromkeys(values))


class RuntimeSettings(Model):
    requests: int = Field(default=50, gt=0)
    details: int = Field(default=30, ge=0)
    interval: float = Field(default=1, ge=0, le=60, allow_inf_nan=False)
    retries: int = Field(default=2, ge=0, le=5)


class SourceSettings(Model):
    enabled: bool = True
    region_code: str | None = Field(default=None, pattern=r"^[0-9]+$")
    town_id: int | None = Field(default=None, gt=0)
    city_id: int | None = Field(default=None, gt=0)
    storage_allowed: bool = False
    interval: float | None = Field(default=None, ge=0, le=60, allow_inf_nan=False)


class SearchConfig(Model):
    schema_version: Literal[2]
    search: SearchSettings
    runtime: RuntimeSettings
    sources: dict[Source, SourceSettings] = Field(
        default_factory=lambda: {
            name: SourceSettings() for name in ("hh", "habr", "trudvsem", "superjob")
        }
    )
    families: dict[
        Literal["product", "business_analyst", "business_assistant", "automation"], list[str]
    ]
    filters: FilterPolicy

    @field_validator("families")
    @classmethod
    def queries(cls, value):
        if not value or any(not items for items in value.values()):
            raise ValueError("empty_query_family")
        for items in value.values():
            if any(not q.strip() or len(q) > 2000 for q in items):
                raise ValueError("invalid_query")
        return value


def load_config(path: Path) -> SearchConfig:
    try:
        return SearchConfig.model_validate(tomllib.loads(path.read_text()))
    except (OSError, ValueError, ValidationError):
        raise SourceError("invalid_search_config") from None


def plan(
    config: SearchConfig,
    *,
    queries=None,
    days=None,
    per_page=None,
    refresh_hours=None,
    now=None,
    source: Source = "hh",
):
    if source not in config.sources or not config.sources[source].enabled:
        raise SourceError("source_disabled")
    queries = list(dict.fromkeys(queries)) if queries is not None else None
    origins = {}
    if queries is None:

        def add(query, family):
            if query not in origins:
                origins[query] = []
            if family not in origins[query]:
                origins[query].append(family)

        if config.search.focused_queries and source == "hh":

            def quote(term):
                return '"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"'

            focus = " OR ".join(quote(x) for x in config.search.focus_terms)
            for family, items in config.families.items():
                add("(" + quote(items[0]) + ") AND (" + focus + ")", family)
        max_len = max(map(len, config.families.values()))
        for index in range(max_len):
            for family, items in config.families.items():
                if index < len(items):
                    add(items[index], family)
        queries = list(origins)
    else:
        origins = {q: ["manual"] for q in queries}
    if not queries or any(not q.strip() or len(q) > 2000 for q in queries):
        raise SourceError("invalid_query")
    end = (now or datetime.now(UTC)).replace(microsecond=0)
    values = config.search.model_dump()
    values.update(
        {
            k: v
            for k, v in {"days": days, "per_page": per_page, "refresh_hours": refresh_hours}.items()
            if v is not None
        }
    )
    try:
        settings = SearchSettings.model_validate(values)
        start = end - timedelta(days=settings.days)
    except (ValueError, OverflowError):
        raise SourceError("invalid_search_settings") from None
    return {
        "source": source,
        "queries": queries,
        "query_families": origins,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "per_page": settings.per_page,
        "ttl_hours": settings.refresh_hours,
        "source_options": (
            {"area_ids": settings.area_ids}
            if source == "hh"
            else config.sources[source].model_dump(mode="json", exclude_none=True)
        ),
        "window_field": {
            "hh": "publication_time",
            "superjob": "date_published",
            "trudvsem": "date_modify",
            "habr": "all_visible",
        }[source],
        "runtime": {
            **config.runtime.model_dump(mode="json"),
            **(
                {"interval": config.sources[source].interval}
                if config.sources[source].interval is not None
                else {}
            ),
        },
        "filter_policy": config.filters.model_dump(mode="json"),
        "filter_policy_hash": config.filters.key(),
        "filter_engine": ENGINE_VERSION,
        "config_hash": digest(config.model_dump(mode="json")),
    }
