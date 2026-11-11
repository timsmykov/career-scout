"""Common vacancy v2. Unknowns remain explicit; there is no candidate filtering."""

import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)

from career_scout.common import digest

Source = Literal["hh", "habr", "superjob", "trudvsem"]
Money = Annotated[StrictInt | StrictFloat, Field(ge=0, allow_inf_nan=False)]
Period = Literal["hour", "shift", "day", "week", "month", "year", "rotation", "service", "project"]
WorkMode = Literal["on_site", "remote", "hybrid", "field_work", "rotation"]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(Model):
    source: Source
    source_id: str = Field(min_length=1, max_length=4096, strict=True)

    @model_validator(mode="after")
    def validate_id(self):
        if not self.source_id.strip():
            raise ValueError("empty_source_id")
        if self.source == "trudvsem":
            try:
                parts = json.loads(self.source_id)
            except ValueError:
                raise ValueError("trudvsem_requires_company_and_id") from None
            if (
                not isinstance(parts, list)
                or len(parts) != 2
                or any(not isinstance(x, str) or not x.strip() for x in parts)
                or json.dumps(parts, ensure_ascii=False, separators=(",", ":")) != self.source_id
            ):
                raise ValueError("noncanonical_trudvsem_identity")
        return self


def source_identity(
    source: Source, vacancy_id: str, *, company_code: str | None = None
) -> Identity:
    if source == "trudvsem":
        if not isinstance(company_code, str) or not company_code.strip():
            raise ValueError("trudvsem_requires_company_code")
        if not isinstance(vacancy_id, str) or not vacancy_id.strip():
            raise ValueError("invalid_vacancy_id")
        vacancy_id = json.dumps(
            [company_code, vacancy_id], ensure_ascii=False, separators=(",", ":")
        )
    return Identity(source=source, source_id=vacancy_id)


class Salary(Model):
    amount_from: Money | None = None
    amount_to: Money | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    gross: StrictBool | None = None
    period: Period | None = None
    payout_frequency: (
        Literal["daily", "weekly", "twice_monthly", "monthly", "per_project"] | None
    ) = None


class Experience(Model):
    category: Literal["no_experience", "one_to_three", "three_to_six", "over_six"] | None = None
    min_years: Money | None = None
    max_years: Money | None = None
    min_inclusive: StrictBool | None = None
    max_inclusive: StrictBool | None = None


class Attributes(Model):
    salary: Salary | None = None
    experience: Experience | None = None
    work_formats: list[WorkMode] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    field_sources: dict[str, list[str]] = Field(default_factory=dict)


class Vacancy(Identity):
    schema_version: Literal[2] = 2
    title: str = Field(min_length=1)
    company: str | None
    url: str = Field(min_length=1)
    description: str = Field(min_length=1)
    description_html: str | None
    source_payload: dict
    conditions: dict
    attributes: Attributes
    published_at: str | None
    created_at: str | None
    source_updated_at: str | None
    detail_fetched_at: str
    detail_status: Literal["full", "archived"]
    content_hash: Hash
    source_normalizer_version: str
    normalizer_version: Literal["common-1"] = "common-1"
    normalization_hash: Hash

    @model_validator(mode="after")
    def verify_normalization_hash(self):
        expected = digest(
            {
                "source": self.source,
                "source_id": self.source_id,
                "content_hash": self.content_hash,
                "normalizer": self.normalizer_version,
                "attributes": self.attributes.model_dump(mode="json"),
            }
        )
        if self.normalization_hash != expected:
            raise ValueError("normalization_hash_mismatch")
        return self
