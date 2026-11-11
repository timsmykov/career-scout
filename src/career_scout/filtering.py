"""Conservative deterministic eligibility. Salary never participates in decisions."""

import re
from typing import Literal

from pydantic import Field

from career_scout.models import Model
from career_scout.search_config import ENGINE_VERSION, FilterPolicy


class Evidence(Model):
    field: str
    quote: str


class Criterion(Model):
    status: Literal["allowed", "excluded", "unknown", "conflict"]
    reasons: list[str]
    evidence: list[Evidence] = Field(default_factory=list)


class Decision(Model):
    engine_version: str = ENGINE_VERSION
    policy_hash: str
    status: Literal["KEEP", "KEEP_UNKNOWN", "FILTERED_OUT"]
    criteria: dict[str, Criterion]


def match_term(text, term):
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I)


def grade(vacancy, policy):
    title = vacancy["title"]
    patterns = {
        "intern": r"\b(?:intern(?:ship)?|стаж[её]р\w*)\b",
        "junior": r"\b(?:junior|jun|джуниор|младш\w*)\b",
        "middle": r"\b(?:middle|mid|мидл)\b",
        "senior": (
            r"\b(?:senior|sr\.?)\b|\b(?:старш\w*|ведущ\w*)\s+"
            r"(?:бизнес[ -]?)?(?:аналитик|инженер|разработчик|специалист|менеджер)\w*"
        ),
        "lead": (
            r"\b(?:team[ -]?lead|тимлид)\b|"
            r"\blead\b(?!\s+(?:generation|generator|gen)\b)"
        ),
        "head": (
            r"^\s*(?:head\b|director\b|директор\b|руководитель\s+"
            r"(?:отдела|департамента|направления|команды|группы)\b)"
        ),
    }
    matches = [
        (key, m) for key, pattern in patterns.items() if (m := re.search(pattern, title, re.I))
    ]
    recipient = re.search(r"\b(?:assistant|ассистент\w*|помощник\w*)\b", title, re.I)
    if recipient:
        # Later grades can describe the person supported, rather than the assistant.
        matches = [(key, m) for key, m in matches if m.start() < recipient.start()]
    negated = re.search(r"\b(?:не|not|без)\s+(?:senior|junior|middle|lead|стаж[её]р)", title, re.I)
    source_grade = None
    if vacancy.get("source") == "habr":
        raw_grade = (vacancy.get("source_payload", {}).get("vacancy") or {}).get("qualification")
        source_grade = (
            {
                "Intern": "intern",
                "Junior": "junior",
                "Middle": "middle",
                "Senior": "senior",
                "Lead": "lead",
            }.get(raw_grade)
            if isinstance(raw_grade, str)
            else None
        )
    if source_grade:
        allowed = source_grade in policy.allowed_grades
        title_allowed = [key in policy.allowed_grades for key, _ in matches]
        conflict = negated or (title_allowed and any(x != allowed for x in title_allowed))
        return Criterion(
            status="conflict" if conflict else "allowed" if allowed else "excluded",
            reasons=[
                "grade.api_title_disagree"
                if conflict
                else "grade.allowed"
                if allowed
                else "grade.outside_allowed"
            ],
            evidence=[Evidence(field="source_payload.vacancy.qualification", quote=raw_grade)]
            + ([Evidence(field="title", quote=m.group(0)) for _, m in matches] if conflict else [])
            + ([Evidence(field="title", quote=negated.group(0))] if negated else []),
        )
    if not matches:
        return Criterion(status="unknown", reasons=["grade.not_stated"])
    evidence = [Evidence(field="title", quote=m.group(0)) for _, m in matches]
    allowed = [key in policy.allowed_grades for key, _ in matches]
    if negated or (any(allowed) and not all(allowed)):
        return Criterion(status="conflict", reasons=["grade.mixed_or_negated"], evidence=evidence)
    return Criterion(
        status="allowed" if all(allowed) else "excluded",
        reasons=["grade.allowed" if all(allowed) else "grade.outside_allowed"],
        evidence=evidence,
    )


OPTIONAL = re.compile(
    r"желатель|приветству|будет плюсом|будет преимуществ|необязател|не обязател|"
    r"не требуется|не нужен|not required|not mandatory|preferred|nice to have|would be a plus",
    re.I,
)
NUMBER = r"(\d{1,2}(?:[.,]\d+)?)"
REQUIRED = [
    re.compile(
        r"^(?:(?:требуется|необходим|обязателен)\s+)?опыт\b.{0,100}?\b(?:от|не менее|минимум)\s*"
        + NUMBER
        + r"(?:\s*(?:до|[-–])\s*\d{1,2}(?:[.,]\d+)?)?\s*(?:лет|года?|год)\b",
        re.I,
    ),
    re.compile(r"^опыт\b.{0,100}?\b" + NUMBER + r"\s*\+\s*(?:лет|года?)\b", re.I),
    re.compile(r"^" + NUMBER + r"\s*\+\s*(?:лет\s+опыта|years?\s+(?:of\s+)?experience)\b", re.I),
    re.compile(
        r"^(?:work\s+)?experience\b.{0,100}?\b(?:at least|minimum|from)\s*"
        + NUMBER
        + r"\s*years?\b",
        re.I,
    ),
]


def experience(vacancy, policy):
    category = (vacancy.get("attributes", {}).get("experience") or {}).get("category")
    base = (
        category in policy.allowed_experience
        if category in {"no_experience", "one_to_three", "three_to_six", "over_six"}
        else None
    )
    evidence = []
    if category:
        evidence.append(Evidence(field="attributes.experience.category", quote=category))
    if base is None:
        structured = vacancy.get("attributes", {}).get("experience") or {}
        minimum = structured.get("min_years")
        if type(minimum) in (int, float):
            base = minimum <= policy.max_required_years
            if minimum == policy.max_required_years and structured.get("min_inclusive") is False:
                base = False
            evidence.append(Evidence(field="attributes.experience.min_years", quote=str(minimum)))
    thresholds = []
    for clause in re.split(r"[\n;!?]|(?<!\d)\.(?!\d)", vacancy["description"]):
        clause = clause.strip().lstrip("-•* ").strip()
        if re.match(r"^(?:опыт (?:работы )?не требуется|без опыта)\b", clause, re.I):
            thresholds.append(0)
            evidence.append(Evidence(field="description", quote=clause))
            continue
        if OPTIONAL.search(clause):
            continue
        for pattern in REQUIRED:
            if m := pattern.search(clause):
                thresholds.append(float(m.group(1).replace(",", ".")))
                evidence.append(Evidence(field="description", quote=m.group(0)))
                break
    if 0 in thresholds and any(x > policy.max_required_years for x in thresholds):
        return Criterion(
            status="conflict", reasons=["experience.text_disagrees"], evidence=evidence
        )
    textual = max(thresholds) <= policy.max_required_years if thresholds else None
    if base is not None and textual is not None and base != textual:
        return Criterion(
            status="conflict", reasons=["experience.api_text_disagree"], evidence=evidence
        )
    decision = textual if textual is not None else base
    if decision is None:
        return Criterion(status="unknown", reasons=["experience.not_stated"], evidence=evidence)
    return Criterion(
        status="allowed" if decision else "excluded",
        reasons=["experience.allowed" if decision else "experience.outside_allowed"],
        evidence=evidence,
    )


def domain(vacancy, policy):
    raw = vacancy.get("source_payload", {})
    employer = raw.get("employer") if isinstance(raw.get("employer"), dict) else {}
    fields = [("title", vacancy["title"]), ("description", vacancy["description"])]
    native = raw.get("vacancy") if vacancy.get("source") == "habr" else raw
    if isinstance(native, dict):
        prefix = "source_payload.vacancy" if vacancy.get("source") == "habr" else "source_payload"
        company_key = "company" if native.get("company") else "client"
        company = native.get(company_key)
        if isinstance(company, dict):
            for key in ("name", "title"):
                if isinstance(company.get(key), str):
                    fields.append((prefix + "." + company_key + "." + key, company[key]))
        if isinstance(native.get("firm_name"), str):
            fields.append((prefix + ".firm_name", native["firm_name"]))
    if isinstance(employer.get("name"), str):
        fields.append(("source_payload.employer.name", employer["name"]))
    industries = []
    for prefix, items in [
        ("source_payload.industries", raw.get("industries")),
        ("source_payload.employer.industries", employer.get("industries")),
    ]:
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str):
                path = f"{prefix}.{index}.name"
                industries.append((path, name))
                fields.append((path, name))
    evidence = []
    for field, text in fields:
        for term in policy.domain_terms:
            if m := match_term(text, term):
                evidence.append(Evidence(field=field, quote=m.group(0)))
                break
    if evidence:
        return Criterion(status="allowed", reasons=["domain.technology_signal"], evidence=evidence)
    outside_title = next(
        (
            m
            for term in policy.outside_role_names
            if (m := match_term(vacancy["title"], term))
            and not vacancy["title"][: m.start()].strip()
        ),
        None,
    )
    all_outside = industries and all(
        name.casefold() in {x.casefold() for x in policy.outside_industry_names}
        for _, name in industries
    )
    if outside_title and all_outside:
        return Criterion(
            status="excluded",
            reasons=["domain.explicit_outside_role_and_industry"],
            evidence=[Evidence(field="title", quote=outside_title.group(0))]
            + [Evidence(field=path, quote=name) for path, name in industries],
        )
    return Criterion(status="unknown", reasons=["domain.not_confirmed"])


def evaluate(vacancy: dict, policy: FilterPolicy) -> Decision:
    criteria = {
        "domain": domain(vacancy, policy),
        "grade": grade(vacancy, policy),
        "experience": experience(vacancy, policy),
    }
    states = [x.status for x in criteria.values()]
    status = (
        "FILTERED_OUT"
        if "excluded" in states
        else "KEEP"
        if all(x == "allowed" for x in states)
        else "KEEP_UNKNOWN"
    )
    return Decision(policy_hash=policy.key(), status=status, criteria=criteria)
