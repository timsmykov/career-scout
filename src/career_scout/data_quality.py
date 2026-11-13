"""Non-destructive, source-independent facts; never infer candidate suitability."""

from decimal import Decimal

from career_scout.common import digest
from career_scout.models import Salary, Vacancy
from career_scout.normalization import project_snapshot


def salary_facts(salary: Salary | None):
    """Keep unknown units/taxes separate; payment frequency never changes amounts."""
    if salary is None:
        return {"status": "unknown", "reasons": ["salary.missing"], "monthly": None}
    reasons = []
    low, high = salary.amount_from, salary.amount_to
    if low is None and high is None:
        reasons.append("salary.amounts_missing")
    if low is not None and high is not None and low > high:
        reasons.append("salary.reversed_bounds")
    for field in ("currency", "period", "gross"):
        if getattr(salary, field) is None:
            reasons.append("salary." + field + "_unknown")
    if salary.period not in (None, "month", "year"):
        reasons.append("salary.monthly_requires_workload")
    monthly = None
    if salary.period in ("month", "year") and "salary.reversed_bounds" not in reasons:
        divisor = Decimal(12 if salary.period == "year" else 1)
        monthly = {
            "from": str(Decimal(str(low)) / divisor) if low is not None else None,
            "to": str(Decimal(str(high)) / divisor) if high is not None else None,
            "currency": salary.currency,
            "gross": salary.gross,
        }
    return {
        "status": "conflict"
        if "salary.reversed_bounds" in reasons
        else "partial"
        if reasons
        else "known",
        "reasons": reasons,
        "monthly": monthly,
        "original": salary.model_dump(mode="json"),
        "tax_conversion": "none",
        "currency_conversion": "none",
    }


def shape(value):
    """Schema observation contains field names/types, never values or credentials."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        variants = {digest(shape(item)): shape(item) for item in value}
        return {"array_variants": [variants[key] for key in sorted(variants)]}
    return type(value).__name__


def inspect_quality(value):
    vacancy = Vacancy.model_validate(project_snapshot(value))
    return {
        "schema_version": 1,
        "source": vacancy.source,
        "salary": salary_facts(vacancy.attributes.salary),
        "issues": vacancy.attributes.issues,
        "conflicts": vacancy.attributes.conflicts,
        "field_sources": vacancy.attributes.field_sources,
        "source_contract_signature": digest(shape(vacancy.source_payload)),
    }
