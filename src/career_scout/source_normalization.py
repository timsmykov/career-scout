"""Source-specific conditions projected without inventing salary periods or experience ranges."""

import math
from datetime import UTC, datetime
from html import escape

from pydantic import ValidationError

from career_scout.common import SourceError, digest, utcnow
from career_scout.html import description_text
from career_scout.models import Attributes, Experience, Salary, Vacancy, source_identity


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def timestamp(value):
    if type(value) in (int, float) and math.isfinite(value):
        try:
            return datetime.fromtimestamp(value, UTC).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return value if isinstance(value, str) and value else None


def section_html(sections):
    return "\n".join(
        "<h2>" + escape(label) + "</h2>\n" + text
        for label, text in sections
        if isinstance(text, str) and text.strip()
    )


def common_record(
    source,
    vid,
    title,
    company,
    html,
    raw,
    conditions,
    attrs,
    url,
    *,
    published=None,
    created=None,
    updated=None,
    archived=False,
):
    if not isinstance(title, str) or not title.strip():
        raise SourceError("detail_schema_drift")
    if not isinstance(html, str) or not description_text(html):
        raise SourceError("detail_missing_description")
    version = source + "-1"
    content_hash = digest(
        {
            "normalizer": version,
            "title": title,
            "company": company,
            "description": html,
            "conditions": conditions,
            "archived": archived,
        }
    )
    values = dict(
        source=source,
        source_id=vid,
        title=title,
        company=company,
        url=url,
        description=description_text(html),
        description_html=html,
        source_payload=raw,
        conditions=conditions,
        attributes=attrs,
        published_at=timestamp(published),
        created_at=timestamp(created),
        source_updated_at=timestamp(updated),
        detail_fetched_at=utcnow(),
        detail_status="archived" if archived else "full",
        content_hash=content_hash,
        source_normalizer_version=version,
        normalization_hash=digest(
            {
                "source": source,
                "source_id": vid,
                "content_hash": content_hash,
                "normalizer": "common-1",
                "attributes": attrs.model_dump(mode="json"),
            }
        ),
    )
    try:
        return Vacancy(**values).model_dump(mode="json")
    except ValidationError:
        raise SourceError("detail_schema_drift") from None


def salary(attrs, low, high, currency, paths):
    lo, hi = number(low), number(high)
    if (low is not None and lo is None) or (high is not None and hi is None):
        attrs.issues.append("salary.invalid_number")
    if lo is None and hi is None:
        return
    codes = {"rub": "RUB", "rur": "RUB", "«руб.»": "RUB", "руб.": "RUB", "usd": "USD", "eur": "EUR"}
    code = codes.get(currency.lower()) if isinstance(currency, str) else None
    if currency is not None and code is None:
        attrs.issues.append("salary.currency.unknown_value")
    attrs.salary = Salary(amount_from=lo, amount_to=hi, currency=code)
    attrs.field_sources["salary"] = paths
    if lo is not None and hi is not None and lo > hi:
        attrs.conflicts.append("salary.reversed_bounds")


def minimum_experience(attrs, value, path):
    if value is None:
        return
    minimum = number(value)
    if minimum is None:
        attrs.issues.append("experience.unknown_value")
        return
    # A minimum of 3 years is not the interval "3–6 years".
    attrs.experience = Experience(min_years=minimum, min_inclusive=True)
    attrs.field_sources["experience"] = [path]


def trudvsem_record(raw, expected):
    company = raw.get("company") or {}
    if not isinstance(company, dict):
        raise SourceError("detail_schema_drift")
    try:
        actual = source_identity(
            "trudvsem", raw.get("id"), company_code=company.get("companycode")
        ).source_id
    except ValueError:
        raise SourceError("detail_identity_mismatch") from None
    if actual != expected:
        raise SourceError("detail_identity_mismatch")
    requirement = raw.get("requirement") or {}
    if not isinstance(requirement, dict):
        raise SourceError("detail_schema_drift")
    sections = [
        ("Обязанности", raw.get("duty")),
        ("Требования", raw.get("requirements")),
        ("Квалификация", requirement.get("qualification")),
    ]
    if any(text is not None and not isinstance(text, str) for _, text in sections):
        raise SourceError("detail_schema_drift")
    if not any(isinstance(text, str) and description_text(text) for _, text in sections):
        raise SourceError("detail_missing_description")
    term = raw.get("term") or {}
    sections.extend(
        [
            ("Условия", term.get("text") if isinstance(term, dict) else None),
            ("Занятость", raw.get("employment")),
            ("График", raw.get("schedule")),
        ]
    )
    attrs = Attributes()
    salary(
        attrs,
        raw.get("salary_min"),
        raw.get("salary_max"),
        raw.get("currency"),
        ["source_payload.salary_min", "source_payload.salary_max", "source_payload.currency"],
    )
    minimum_experience(
        attrs, requirement.get("experience"), "source_payload.requirement.experience"
    )
    modes = {
        "Удаленная работа": "remote",
        "Дистанционная работа": "remote",
        "Вахтовый метод": "rotation",
    }
    if raw.get("schedule") in modes:
        attrs.work_formats = [modes[raw["schedule"]]]
        attrs.field_sources["work_formats"] = ["source_payload.schedule"]
    keys = (
        "salary_min",
        "salary_max",
        "salary",
        "currency",
        "employment",
        "schedule",
        "requirement",
        "region",
        "addresses",
        "category",
        "skills",
        "conditions",
        "social_protected",
        "term",
    )
    return common_record(
        "trudvsem",
        expected,
        raw.get("job-name"),
        company.get("name"),
        section_html(sections),
        raw,
        {k: raw.get(k) for k in keys},
        attrs,
        raw.get("vac_url")
        or "https://trudvsem.ru/vacancy/card/" + company["companycode"] + "/" + raw["id"],
        published=raw.get("creation-date"),
        created=raw.get("creation-date"),
        updated=raw.get("date_modify"),
    )


def superjob_record(raw, expected):
    if type(raw.get("id")) is not int or str(raw["id"]) != expected:
        raise SourceError("detail_identity_mismatch")
    rich = raw.get("vacancyRichText")
    parts = [
        ("Обязанности", raw.get("work")),
        ("Требования", raw.get("candidat")),
        ("Условия", raw.get("compensation")),
    ]
    if any(text is not None and not isinstance(text, str) for _, text in parts):
        raise SourceError("detail_schema_drift")
    if rich is not None and not isinstance(rich, str):
        raise SourceError("detail_schema_drift")
    # Retain each fallback section even when rich text is present but shorter.
    html = rich if isinstance(rich, str) and description_text(rich) else ""
    for label, text in parts:
        if isinstance(text, str) and description_text(text):
            if description_text(text) not in description_text(html):
                html += "\n" + section_html([(label, text)])
    attrs = Attributes()
    if not raw.get("agreement"):
        low = raw.get("payment_from") or None
        high = raw.get("payment_to") or None
        salary(
            attrs,
            low,
            high,
            raw.get("currency"),
            ["source_payload.payment_from", "source_payload.payment_to", "source_payload.currency"],
        )
    exp = raw.get("experience") or {}
    if isinstance(exp, dict):
        minimum_experience(
            attrs, {1: 0, 2: 1, 3: 3, 4: 6}.get(exp.get("id")), "source_payload.experience.id"
        )
        if exp.get("id") not in (None, 0, 1, 2, 3, 4):
            attrs.issues.append("experience.unknown_value")
    mode = raw.get("place_of_work") or {}
    if isinstance(mode, dict) and mode.get("id") in (1, 2, 3):
        attrs.work_formats = [{1: "on_site", 2: "remote", 3: "field_work"}[mode["id"]]]
        attrs.field_sources["work_formats"] = ["source_payload.place_of_work.id"]
    firm = raw.get("client") or {}
    company = (firm.get("title") if isinstance(firm, dict) else None) or raw.get("firm_name")
    keys = (
        "payment_from",
        "payment_to",
        "currency",
        "agreement",
        "experience",
        "type_of_work",
        "place_of_work",
        "town",
        "catalogues",
        "education",
        "business_trip",
    )
    return common_record(
        "superjob",
        expected,
        raw.get("profession"),
        company,
        html,
        raw,
        {k: raw.get(k) for k in keys},
        attrs,
        raw.get("link") or "https://www.superjob.ru/vakansii/" + expected + ".html",
        published=raw.get("date_published"),
        archived=raw.get("is_archive") is True,
    )


def habr_record(vacancy, job, expected):
    if not isinstance(job.get("identifier"), dict):
        raise SourceError("detail_schema_drift")
    if (
        str(vacancy.get("id")) != expected
        or str((job.get("identifier") or {}).get("value")) != expected
    ):
        raise SourceError("detail_identity_mismatch")
    if job.get("title") != vacancy.get("title"):
        raise SourceError("detail_title_drift")
    html = vacancy.get("description")
    if not isinstance(html, str) or not description_text(html):
        raise SourceError("detail_missing_description")
    # SSR description is the complete rendered card body; JSON-LD is retained
    # separately as provenance and must not duplicate that body after escaping.
    company = vacancy.get("company") or {}
    org = job.get("hiringOrganization") or {}
    if not isinstance(company, dict) or not isinstance(org, dict):
        raise SourceError("detail_schema_drift")
    if org.get("name") and company.get("title") and org["name"] != company["title"]:
        raise SourceError("detail_company_drift")
    attrs = Attributes()
    pay = vacancy.get("salary") or {}
    if isinstance(pay, dict):
        salary(
            attrs,
            pay.get("from"),
            pay.get("to"),
            pay.get("currency"),
            ["source_payload.vacancy.salary"],
        )
    if vacancy.get("remoteWork") is True or job.get("jobLocationType") == "TELECOMMUTE":
        attrs.work_formats = ["remote"]
        attrs.field_sources["work_formats"] = [
            "source_payload.vacancy.remoteWork",
            "source_payload.job_posting.jobLocationType",
        ]
    conditions = {
        k: vacancy.get(k)
        for k in (
            "salary",
            "qualification",
            "employment",
            "remoteWork",
            "locations",
            "skills",
            "divisions",
            "archived",
            "hidden",
        )
    }
    conditions["valid_through"] = job.get("validThrough")
    return common_record(
        "habr",
        expected,
        vacancy.get("title"),
        company.get("title"),
        html,
        {"vacancy": vacancy, "job_posting": job},
        conditions,
        attrs,
        "https://career.habr.com/vacancies/" + expected,
        published=job.get("datePosted"),
        archived=vacancy.get("archived") is True or vacancy.get("hidden") is True,
    )


def listing_hash(source, item):
    """Viewer counters, reactions and contact metadata do not invalidate a full card."""
    fields = {
        "habr": (
            "id",
            "title",
            "salary",
            "qualification",
            "remoteWork",
            "employment",
            "locations",
            "skills",
            "divisions",
            "archived",
            "hidden",
            "description",
        ),
        "trudvsem": (
            "id",
            "job-name",
            "duty",
            "requirements",
            "requirement",
            "salary_min",
            "salary_max",
            "salary",
            "currency",
            "employment",
            "schedule",
            "region",
            "addresses",
            "category",
            "skills",
            "conditions",
            "social_protected",
            "term",
            "date_modify",
        ),
        "superjob": (
            "id",
            "profession",
            "work",
            "candidat",
            "compensation",
            "vacancyRichText",
            "payment_from",
            "payment_to",
            "currency",
            "agreement",
            "experience",
            "type_of_work",
            "place_of_work",
            "town",
            "catalogues",
            "education",
            "business_trip",
            "is_archive",
        ),
    }
    content = {key: item.get(key) for key in fields[source]}
    company = item.get("company") or item.get("client") or {}
    content["company"] = (
        {key: company.get(key) for key in ("id", "companycode", "name", "title")}
        if isinstance(company, dict)
        else None
    )
    content["firm_name"] = item.get("firm_name")
    return digest(content)
