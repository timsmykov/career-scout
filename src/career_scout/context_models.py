"""Portable connections and explicit execution settings. No credentials in this schema."""

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from career_scout.evaluation import EvaluatorConfig
from career_scout.models import Hash, Identity, Model


class NativeConnection(Model):
    binary: str = Field(default="/usr/local/bin/openbot-openai-api", min_length=1)
    workspace: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1)


class EvaluationConnection(Model):
    model: EvaluatorConfig = Field(default_factory=EvaluatorConfig)
    decisions: NativeConnection | None = None
    external_calls_enabled: StrictBool = False
    batch_size: int = Field(default=20, ge=1, le=20, strict=True)
    workers: int = Field(default=4, ge=1, le=4, strict=True)
    freshness_days: int = Field(default=14, ge=1, le=3650, strict=True)
    max_batch_characters: int = Field(default=200000, ge=1000, le=2000000, strict=True)
    max_decision_characters: int = Field(default=200000, ge=1000, le=2000000, strict=True)
    routing_margin: float = Field(default=0.10, ge=0, le=1, allow_inf_nan=False)
    allow_decisions_exclusion: StrictBool = False
    exclusion_threshold: float = Field(default=0.995, ge=0.99, le=1, allow_inf_nan=False)


class FactRule(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    field: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.]{0,150}$")
    operator: Literal["eq", "in", "not_in", "gte", "lte"]
    value: str | float | int | bool | list[str]
    guards: dict[str, str | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_operand(self):
        if self.field.startswith("attributes.salary.amount_") and not {
            "attributes.salary.currency",
            "attributes.salary.period",
        }.issubset(self.guards):
            raise ValueError("salary_rule_requires_currency_and_period_guards")
        if self.operator in {"in", "not_in"} and not isinstance(self.value, list):
            raise ValueError("membership_requires_list")
        if self.operator in {"gte", "lte"} and type(self.value) not in (float, int):
            raise ValueError("numeric_rule_requires_number")
        return self


class QualityCase(Identity):
    expected: Literal["PASS", "UNCERTAIN", "REJECT"]


class QualityLabels(Model):
    context_hash: Hash
    cases: list[QualityCase] = Field(min_length=1, max_length=10000)
    split: Literal["development", "holdout", "unspecified"] = "unspecified"
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({(c.source, c.source_id) for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate_quality_case")
        return self
