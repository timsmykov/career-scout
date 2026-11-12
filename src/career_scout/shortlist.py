"""User-facing output contains only validated application recommendations."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from career_scout.evaluation import EvaluationError

NAMES = {
    "product": "Product",
    "automation": "Автоматизация / AI",
    "business_assistant": "Бизнес-ассистент",
    "business_analyst": "Бизнес-аналитик",
}
WORK_FORMATS = {
    "remote": "удалённо",
    "hybrid": "гибрид",
    "on_site": "офис",
    "field_work": "разъездная работа",
    "rotation": "вахта",
}


def terms(vacancy):
    labels = [WORK_FORMATS[m] for m in vacancy.attributes.work_formats if m in WORK_FORMATS]
    if "remote" not in vacancy.attributes.work_formats:
        for key in ("area", "town", "region"):
            value = vacancy.conditions.get(key)
            if isinstance(value, dict) and (city := value.get("name") or value.get("title")):
                labels.append(str(city))
                break
    salary = vacancy.attributes.salary
    if salary is None:
        return "; ".join(labels)
    amounts = [x for x in (salary.amount_from, salary.amount_to) if x is not None]
    if amounts:
        numbers = "–".join(
            (f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}".rstrip("0")).replace(",", " ")
            for x in dict.fromkeys(amounts)
        )
        if salary.amount_to is None:
            numbers = "от " + numbers
        elif salary.amount_from is None:
            numbers = "до " + numbers
        currency = "₽" if salary.currency in {"RUB", "RUR"} else salary.currency or ""
        tax = " на руки" if salary.gross is False else " до налогов" if salary.gross is True else ""
        periods = {
            "month": "в месяц",
            "hour": "в час",
            "shift": "за смену",
            "day": "в день",
            "week": "в неделю",
            "year": "в год",
            "rotation": "за вахту",
            "service": "за услугу",
            "project": "за проект",
        }
        period = " " + periods[salary.period] if salary.period in periods else ""
        labels.append(numbers + " " + currency + period + tax)
    return "; ".join(labels)


def publication_time(value, now):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    cutoff = now - timedelta(days=14)
    if len(value) == 10:
        # Date-only publication cannot prove the cutoff hour on the boundary day.
        return stamp.replace(tzinfo=UTC) if cutoff.date() < stamp.date() <= now.date() else None
    return stamp if stamp.tzinfo and cutoff <= stamp <= now else None


def recommendations(store, *, run_id=None, profile=None, limit=20, now=None):
    now = now or datetime.now(UTC)
    if limit < 1:
        raise EvaluationError("invalid_shortlist_limit")
    if run_id:
        frozen = json.loads(store.run(run_id)["config_json"])
    else:
        row = store.db.execute(
            "SELECT config_json FROM analysis_runs "
            "WHERE json_extract(config_json,'$.evaluation_policy')='applications-1' "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        frozen = json.loads(row[0])
    if frozen.get("evaluation_policy") != "applications-1":
        raise EvaluationError("shortlist_requires_application_policy")
    rows = store.db.execute(
        "SELECT j.*,r.config_json,r.created_at FROM run_jobs j "
        "JOIN analysis_runs r ON r.id=j.run_id ORDER BY r.created_at DESC,r.rowid DESC,j.position"
    ).fetchall()
    seen, output = set(), []
    for row in rows:
        config = json.loads(row["config_json"])
        if run_id and row["run_id"] != run_id:
            continue
        if config.get("selection_key") != frozen.get("selection_key"):
            continue
        inputs = store.inputs(row["route_key"])
        vacancy = inputs[0].vacancy
        identity = (vacancy.source, vacancy.source_id)
        if identity in seen:
            continue
        seen.add(identity)
        if row["status"] != "ACCEPTED" or not row["outcome_json"]:
            continue
        outcome = json.loads(row["outcome_json"])
        chosen = outcome.get("accepted_profile")
        if profile:
            if profile not in outcome.get("selected_profiles", [chosen]):
                continue
            chosen = profile
        request = next((r for r in inputs if r.profile_id == chosen), None)
        result = store.cached(request) if request else None
        if not result or result.schema_version != 3 or result.verdict != "PASS":
            continue
        if publication_time(vacancy.published_at, now) is None:
            continue
        if urlsplit(vacancy.url).scheme not in {"http", "https"}:
            continue
        output.append(
            {
                "source": vacancy.source,
                "source_id": vacancy.source_id,
                "title": vacancy.title,
                "company": vacancy.company,
                "url": vacancy.url,
                "profile": chosen,
                "resume_id": request.resume.resume_id,
                "published_at": vacancy.published_at,
                "reason": result.reason,
                "terms": terms(vacancy),
                "evaluation_key": request.evaluation_key,
                "assessments": [a.model_dump(mode="json") for a in result.assessments],
            }
        )
    output.sort(key=lambda r: publication_time(r["published_at"], now), reverse=True)
    return output[:limit]


def render(rows):
    if not rows:
        return "По текущим резюме и параметрам подходящих вакансий в обработанном наборе нет.\n"
    lines = []
    for i, r in enumerate(rows, 1):
        title = r["title"].replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
        lines.append(
            f"{i}. [{title}]({r['url']}) — {r['company'] or 'Работодатель не указан'}\n"
            + (f"   {r['terms']}.\n" if r.get("terms") else "")
            + f"   Резюме: {NAMES[r['profile']]}. {r['reason']}\n"
        )
    return "\n".join(lines)
