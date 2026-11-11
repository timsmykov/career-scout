"""Pure projections from immutable source snapshots to common vacancies."""

import math
import re

from career_scout.common import SourceError, digest
from career_scout.models import Attributes, Experience, Salary, Vacancy

NORMALIZER = "common-1"
PERIODS = {
    "MONTH": "month",
    "HOUR": "hour",
    "SHIFT": "shift",
    "FLY_IN_FLY_OUT": "rotation",
    "SERVICE": "service",
}
FREQUENCIES = {
    "DAILY": "daily",
    "WEEKLY": "weekly",
    "TWICE_PER_MONTH": "twice_monthly",
    "MONTHLY": "monthly",
    "PER_PROJECT": "per_project",
}
WORK_MODES = {
    "ON_SITE": "on_site",
    "REMOTE": "remote",
    "HYBRID": "hybrid",
    "FIELD_WORK": "field_work",
}
EXPERIENCES = {
    "noExperience": ("no_experience", 0, 0, True),
    "between1And3": ("one_to_three", 1, 3, True),
    "between3And6": ("three_to_six", 3, 6, True),
    "moreThan6": ("over_six", 6, None, False),
}


def hh_attributes(raw: dict) -> Attributes:
    out = Attributes()

    def mark(field, path):
        out.field_sources.setdefault(field, []).append("source_payload." + path)

    def mapped(value, mapping, field):
        if value is None:
            return None
        key = value.get("id") if isinstance(value, dict) else None
        result = mapping.get(key) if isinstance(key, str) else None
        if result is None:
            out.issues.append(field + ".unknown_value")
        return result

    # Never mix the modern period-specific amount with legacy salary bounds.
    block = raw.get("salary_range")
    field = "salary_range"
    if block is None:
        block, field = raw.get("salary"), "salary"
    if block is not None:
        mark("salary", field)
        if not isinstance(block, dict):
            out.issues.append("salary.invalid_object")
        else:
            values = {}
            for key, dest in (("from", "amount_from"), ("to", "amount_to")):
                value = block.get(key)
                if value is not None and (
                    type(value) not in (int, float) or not math.isfinite(value) or value < 0
                ):
                    out.issues.append("salary." + key + ".invalid_number")
                    value = None
                values[dest] = value
            currency = block.get("currency")
            if currency is not None:
                if isinstance(currency, str) and re.fullmatch(r"[A-Za-z]{3}", currency):
                    currency = currency.upper()
                    currency = "RUB" if currency == "RUR" else currency
                else:
                    currency = None
                    out.issues.append("salary.currency.invalid_value")
            gross = block.get("gross")
            if gross is not None and type(gross) is not bool:
                gross = None
                out.issues.append("salary.gross.invalid_value")
            period = mapped(block.get("mode"), PERIODS, "salary.period")
            frequency = mapped(block.get("frequency"), FREQUENCIES, "salary.frequency")
            if period is not None:
                mark("salary.period", field + ".mode.id")
            # Legacy salary has no explicit period. Do not infer month from payout frequency.
            out.salary = Salary(
                **values, currency=currency, gross=gross, period=period, payout_frequency=frequency
            )
            if values["amount_from"] is not None and values["amount_to"] is not None:
                if values["amount_from"] > values["amount_to"]:
                    out.conflicts.append("salary.reversed_bounds")

    experience = raw.get("experience")
    if experience is not None:
        mark("experience", "experience")
        values = mapped(experience, EXPERIENCES, "experience")
        if values:
            category, minimum, maximum, inclusive = values
            out.experience = Experience(
                category=category,
                min_years=minimum,
                max_years=maximum,
                min_inclusive=inclusive,
                max_inclusive=True if maximum is not None else None,
            )

    explicit = []
    for field in ("work_format", "work_formats"):
        items = raw.get(field)
        if items is None:
            continue
        mark("work_formats", field)
        modes = []
        if not isinstance(items, list):
            out.issues.append("work_format.invalid_array")
        else:
            for item in items:
                mode = mapped(item, WORK_MODES, "work_format")
                if mode and mode not in modes:
                    modes.append(mode)
        if modes:
            explicit.append(set(modes))
            for mode in modes:
                if mode not in out.work_formats:
                    out.work_formats.append(mode)
    if len(explicit) > 1 and explicit[0] != explicit[1]:
        out.conflicts.append("work_format.fields_disagree")
    schedule = raw.get("schedule")
    legacy_id = schedule.get("id") if isinstance(schedule, dict) else None
    if legacy_id is not None and not isinstance(legacy_id, str):
        out.issues.append("work_format.schedule.invalid_value")
    legacy = (
        {"remote": "remote", "flyInFlyOut": "rotation"}.get(legacy_id)
        if isinstance(legacy_id, str)
        else None
    )
    if legacy:
        mark("work_formats", "schedule.id")
        if out.work_formats and legacy not in out.work_formats:
            out.conflicts.append("work_format.schedule_disagrees")
        elif not out.work_formats:
            out.work_formats.append(legacy)
    # fullDay, flexible and shift describe timing, not a physical work location.
    out.work_formats.sort()
    out.issues = sorted(set(out.issues))
    out.conflicts = sorted(set(out.conflicts))
    return out


def project_snapshot(snapshot: dict) -> dict:
    if snapshot.get("schema_version") == 2:
        return Vacancy.model_validate(snapshot).model_dump(mode="json")
    if snapshot.get("source") != "hh" or snapshot.get("schema_version") != 1:
        raise SourceError("normalization_adapter_unavailable")
    attributes = hh_attributes(snapshot["source_payload"])
    value = {
        key: snapshot[key]
        for key in (
            "source",
            "source_id",
            "url",
            "title",
            "company",
            "description",
            "description_html",
            "source_payload",
            "conditions",
            "published_at",
            "created_at",
            "source_updated_at",
            "detail_fetched_at",
            "detail_status",
            "content_hash",
        )
    }
    value.update(
        attributes=attributes,
        source_normalizer_version=snapshot["normalizer_version"],
        normalization_hash=digest(
            {
                "source": snapshot["source"],
                "source_id": snapshot["source_id"],
                "content_hash": snapshot["content_hash"],
                "normalizer": NORMALIZER,
                "attributes": attributes.model_dump(mode="json"),
            }
        ),
    )
    return Vacancy(**value).model_dump(mode="json")
