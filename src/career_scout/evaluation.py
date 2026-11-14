"""Versioned evaluator inputs and evidence checks, independent of the model transport."""

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_serializer, model_validator

from career_scout.common import digest
from career_scout.models import Hash, Model, Vacancy

ProfileID = Annotated[str, Field(strict=True, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]
ORDER: tuple[ProfileID, ...] = ("product", "automation", "business_assistant", "business_analyst")
EVALUATOR_VERSION = "evaluation-1"


class EvaluationError(Exception):
    """Only safe fixed codes cross the worker boundary."""

    def __init__(self, code, *, fatal=False, usage=None):
        super().__init__(code)
        self.code = code
        self.fatal = fatal
        self.usage = usage


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_quote(text: str) -> str:
    """NFC plus Unicode whitespace folding; never fuzzy or case-insensitive matching."""
    return " ".join(unicodedata.normalize("NFC", text).split())


class ResumeSnapshot(Model):
    profile_id: ProfileID
    resume_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    captured_at: datetime
    content_hash: Hash
    text: str = Field(min_length=1)
    provenance: dict

    @model_validator(mode="after")
    def verified_content(self):
        if not self.text.strip() or text_hash(self.text) != self.content_hash:
            raise ValueError("resume_content_hash_mismatch")
        if self.captured_at.tzinfo is None:
            raise ValueError("resume_capture_requires_timezone")
        return self


class EvaluatorConfig(Model):
    provider: Literal["codex_subscription"] = "codex_subscription"
    model: str = "gpt-6-luna"
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "max"
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_input_characters: int = Field(default=100_000, ge=100, le=2_000_000)
    max_attempts_per_profile: int = Field(default=2, ge=1, le=3)
    executable: str | None = Field(default=None, min_length=1)

    @model_serializer(mode="wrap")
    def compatible_settings(self, handler):
        values = handler(self)
        if values.get("executable") is None:
            values.pop("executable", None)
        return values

    def key(self):
        return digest(self.model_dump(mode="json"))


class WorkerInput(Model):
    schema_version: Literal[2]
    evaluation_key: Hash
    profile_id: ProfileID
    vacancy: Vacancy
    resume: ResumeSnapshot
    filter_version: str = Field(min_length=1)
    filters: dict
    rubric_version: str = Field(min_length=1)
    rubric: str = Field(min_length=1)
    common_instructions: str = Field(min_length=1)
    model_config_hash: Hash
    evaluator_version: Literal["evaluation-1"] = EVALUATOR_VERSION
    evaluation_date: datetime

    def semantic_content(self):
        # evaluation_date records when the request was prepared. Freshness belongs
        # to catalog admission; this timestamp is not sent as a judging instruction.
        return self.model_dump(mode="json", exclude={"evaluation_key", "evaluation_date"})

    @model_validator(mode="after")
    def validate_identity(self):
        if self.resume.profile_id != self.profile_id:
            raise ValueError("resume_profile_mismatch")
        if self.vacancy.detail_status != "full":
            raise ValueError("incomplete_vacancy")
        if self.evaluation_date.tzinfo is None:
            raise ValueError("evaluation_date_requires_timezone")
        if self.evaluation_key != digest(self.semantic_content()):
            raise ValueError("evaluation_key_mismatch")
        return self


def make_input(
    *,
    vacancy,
    resume,
    filters,
    filter_version,
    rubric,
    rubric_version,
    common_instructions,
    config,
    evaluation_date,
):
    values = {
        "schema_version": 2,
        "profile_id": resume.profile_id,
        "vacancy": vacancy.model_dump(mode="json"),
        "resume": resume.model_dump(mode="json"),
        "filter_version": filter_version,
        "filters": filters,
        "rubric": rubric,
        "rubric_version": rubric_version,
        "common_instructions": common_instructions,
        "model_config_hash": config.key(),
        "evaluator_version": EVALUATOR_VERSION,
    }
    return WorkerInput(**values, evaluation_key=digest(values), evaluation_date=evaluation_date)


class Assessment(Model):
    kind: Literal["responsibility", "requirement"]
    requirement: str = Field(min_length=1, max_length=1000)
    mandatory: StrictBool | None
    vacancy_quote: str = Field(min_length=1, max_length=1000)
    resume_quote: str | None = Field(max_length=1000)
    status: Literal["confirmed", "unconfirmed", "conflict", "outside_profile"]
    explanation: str = Field(min_length=1, max_length=1000)


class EvaluationResult(Model):
    schema_version: Literal[2]
    evaluation_key: Hash
    profile_id: ProfileID
    verdict: Literal["PASS", "BORDERLINE", "REJECT"]
    reason: str = Field(min_length=1, max_length=1200)
    assessments: list[Assessment] = Field(min_length=1, max_length=12)
    questions: list[str] = Field(max_length=12)

    @model_validator(mode="after")
    def validate_conclusion(self):
        if any(not q.strip() or len(q) > 500 for q in self.questions):
            raise ValueError("invalid_question")
        if len(set(self.questions)) != len(self.questions):
            raise ValueError("duplicate_question")
        for item in self.assessments:
            if item.status in {"confirmed", "conflict"} and not item.resume_quote:
                raise ValueError("asserted_experience_requires_resume_evidence")
            if item.status == "unconfirmed" and item.resume_quote is not None:
                raise ValueError("unconfirmed_requires_null_resume_quote")
        if self.verdict == "PASS":
            if self.questions or any(
                x.status != "confirmed" and (x.mandatory is not False or x.kind == "responsibility")
                for x in self.assessments
            ):
                raise ValueError("pass_with_unresolved_requirement")
            if not any(x.kind == "responsibility" for x in self.assessments):
                raise ValueError("pass_without_confirmed_responsibility")
        if self.verdict == "BORDERLINE" and not self.questions:
            raise ValueError("borderline_without_question")
        if self.verdict == "REJECT" and not any(
            (x.mandatory is True and x.status == "conflict")
            or (
                x.kind == "responsibility"
                and x.status == "outside_profile"
                and x.mandatory is not False
            )
            for x in self.assessments
        ):
            raise ValueError("reject_without_mandatory_conflict_or_outside_role")
        return self


def vacancy_context(vacancy):
    """Full task text and conditions; API transport metadata is not candidate evidence."""
    return {
        "source": vacancy.source,
        "source_id": vacancy.source_id,
        "url": vacancy.url,
        "title": vacancy.title,
        "company": vacancy.company,
        "description": vacancy.description,
        "attributes": vacancy.attributes.model_dump(mode="json"),
        "conditions": {
            k: v
            for k, v in vacancy.conditions.items()
            if k not in {"name", "description", "employer"}
        },
        "published_at": vacancy.published_at,
    }


def vacancy_evidence_text(vacancy):
    context = vacancy_context(vacancy)
    return (
        vacancy.title
        + "\n"
        + vacancy.description
        + "\n"
        + json.dumps(context["conditions"], ensure_ascii=False, sort_keys=True)
        + "\n"
        + json.dumps(context["attributes"], ensure_ascii=False, sort_keys=True)
    )


def evidence_spans(text):
    """Contiguous original spans; the full input is retained independently."""
    spans = []
    # Preserve nearby labels (for example, skill + its stated level) without
    # asking the model to join separate excerpts into a fabricated quotation.
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if len(paragraph) <= 1000:
            if paragraph:
                spans.append(paragraph)
        else:
            for offset in range(0, len(paragraph), 800):
                spans.append(paragraph[offset : offset + 1000].strip())
    for line in text.splitlines():
        for sentence in re.split(r"(?<=[.!?;])\s+", line):
            sentence = sentence.strip()
            while sentence:
                if len(sentence) <= 1000:
                    spans.append(sentence)
                    break
                boundary = sentence.rfind(" ", 0, 1000)
                boundary = boundary if boundary > 0 else 1000
                spans.append(sentence[:boundary].strip())
                sentence = sentence[boundary:].strip()
    return list(dict.fromkeys(spans))


def validate_result(request: WorkerInput, result: EvaluationResult):
    expected_schema = {"applications-1": 3, "context-1": 4}.get(
        request.filters.get("evaluation_policy"), 2
    )
    if result.schema_version != expected_schema:
        raise EvaluationError("result_policy_mismatch")
    if result.evaluation_key != request.evaluation_key or result.profile_id != request.profile_id:
        raise EvaluationError("result_identity_mismatch")
    vacancy_text = canonical_quote(vacancy_evidence_text(request.vacancy))
    resume_text = canonical_quote(request.resume.text)
    for item in result.assessments:
        quote = canonical_quote(item.vacancy_quote)
        if not quote or quote not in vacancy_text:
            raise EvaluationError("vacancy_quote_not_found")
        if item.resume_quote is not None:
            quote = canonical_quote(item.resume_quote)
            if not quote or quote not in resume_text:
                raise EvaluationError("resume_quote_not_found")
    if expected_schema == 4:
        from career_scout.context_policy import validate_preferences

        validate_preferences(request, result)
    return result
