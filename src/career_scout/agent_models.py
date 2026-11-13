"""Resume-first agent inputs; no professional taxonomy and no inferred experience."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from career_scout.context_models import FactRule
from career_scout.models import Model, Source
from career_scout.search_config import (
    FilterPolicy,
    RuntimeSettings,
    SearchConfig,
    SearchSettings,
    SourceSettings,
    plan,
)


class ResumeInput(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=2_000_000)
    provenance: dict = Field(default_factory=dict)

    @field_validator("title", "text")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("empty_resume")
        return value


class Preference(Model):
    statement: str = Field(min_length=1, max_length=20000)
    priority: Literal["required", "preferred", "avoid"] = "preferred"
    provenance: dict = Field(default_factory=dict)
    structured: dict = Field(default_factory=dict)

    @field_validator("statement")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("empty_preference")
        return value


class ContextInput(Model):
    resumes: list[ResumeInput] = Field(min_length=1, max_length=100)
    goals: str = Field(default="", max_length=100000)
    preferences: list[Preference | str] = Field(default_factory=list, max_length=1000)
    attributes: dict = Field(default_factory=dict)
    interaction_mode: Literal["unattended"] = "unattended"
    fact_rules: list[FactRule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_resumes(self):
        if len({r.id for r in self.resumes}) != len(self.resumes):
            raise ValueError("duplicate_resume_id")
        if len({r.id for r in self.fact_rules}) != len(self.fact_rules):
            raise ValueError("duplicate_rule_id")
        if any(isinstance(p, str) and not p.strip() for p in self.preferences):
            raise ValueError("empty_preference")
        return self


def unrestricted_policy():
    return FilterPolicy(
        allowed_grades=["intern", "junior", "middle", "senior", "lead", "head"],
        allowed_experience=["no_experience", "one_to_three", "three_to_six", "over_six"],
        max_required_years=None,
        domain_terms=[],
    )


class SearchPlanInput(Model):
    sources: list[Source] = Field(min_length=1, max_length=4)
    queries: list[str] = Field(min_length=1, max_length=1000)
    days: int = Field(default=14, ge=1, le=3650, strict=True)
    per_page: int = Field(default=100, ge=1, le=100, strict=True)
    source_options: dict[Source, SourceSettings] = Field(default_factory=dict)
    area_ids: list[str] = Field(default_factory=list)
    collection_policy: FilterPolicy = Field(default_factory=unrestricted_policy)

    @model_validator(mode="after")
    def valid_plan(self):
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("duplicate_source")
        if any(not q.strip() or len(q) > 2000 for q in self.queries):
            raise ValueError("invalid_query")
        if any(s not in self.sources for s in self.source_options):
            raise ValueError("unused_source_options")
        if any(not self.source_options.get(s, SourceSettings()).enabled for s in self.sources):
            raise ValueError("source_disabled")
        return self

    def compile(self, mode="initial", *, now=None):
        now = (now or datetime.now(UTC)).replace(microsecond=0)
        sources = {s: self.source_options.get(s, SourceSettings()) for s in self.sources}
        if "habr" in sources and sources["habr"].interval is None:
            sources["habr"] = sources["habr"].model_copy(update={"interval": 3})
        config = SearchConfig(
            schema_version=2,
            search=SearchSettings(
                days=self.days,
                per_page=self.per_page,
                focused_queries=False,
                focus_terms=[],
                area_ids=self.area_ids,
            ),
            runtime=RuntimeSettings(),
            sources=sources,
            families={"agent": self.queries},
            filters=self.collection_policy,
        )
        result = []
        for source in self.sources:
            frozen = plan(config, source=source, queries=self.queries, now=now)
            frozen.update(
                append_only=True,
                collection_mode=mode,
                publication_start=frozen["start"],
                publication_end=frozen["end"],
                lookback_days=self.days,
            )
            result.append(frozen)
        return result


class RunLimits(Model):
    max_requests: int = Field(default=200, ge=1, le=1_000_000, strict=True)
    chunk_requests: int = Field(default=50, ge=1, le=10000, strict=True)
    max_rounds: int = Field(default=1, ge=1, le=100, strict=True)
