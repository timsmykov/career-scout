"""Read-only source adapters. Explicit schema errors never become empty successful searches."""

import json
import math
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import quote

from pydantic import ValidationError

from career_scout.common import SourceError
from career_scout.models import source_identity
from career_scout.source_normalization import habr_record, superjob_record, trudvsem_record
from career_scout.source_types import Page
from career_scout.transport import ReadClient


def source_page(**values):
    try:
        return Page(**values)
    except ValidationError:
        raise SourceError("pagination_schema_drift") from None


def integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise SourceError("search_schema_drift")
    return value


def utc(value):
    return datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows(value):
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise SourceError("search_schema_drift")
    return value


def numeric_id(value):
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0:
        return value
    raise SourceError("invalid_source_id")


class TrudvsemClient(ReadClient):
    source = "trudvsem"
    base = "https://opendata.trudvsem.ru/api/v1/vacancies"
    max_page_size = 20
    replan_slow_pages = True
    supports_date_split = True
    # Operational partition target, not an API result cap. All child windows
    # remain queued; small pages also work for uncached date ranges.
    partition_target = 300
    partition_page_size = 5

    def envelope(self, body):
        if str(body.get("status")) != "200":
            raise SourceError("source_error_envelope")
        meta = body.get("meta")
        result = body.get("results")
        if not isinstance(meta, dict) or not isinstance(result, dict):
            raise SourceError("search_schema_drift")
        total = integer(meta.get("total"))
        items = rows(result.get("vacancies", []) if total == 0 else result.get("vacancies"))
        if any(not isinstance(item.get("vacancy"), dict) for item in items):
            raise SourceError("search_schema_drift")
        return [item["vacancy"] for item in items], total, meta

    def search(self, query, start, end, page, per_page, *, area_ids=None):
        params = {
            "text": query,
            "offset": page,
            # Live 100-row responses timed out; 20-row pages returned the full
            # objects in under ten seconds. This limits page size, not total IDs.
            "limit": min(per_page, self.max_page_size),
        }
        if start or end:
            # Keep both date bounds explicit, including all available history.
            params["modifiedFrom"] = utc(start or "1970-01-01T00:00:00+00:00")
        if end:
            params["modifiedTo"] = utc(end)
        region = self.options.get("region_code")
        path = self.base + ("/region/" + quote(region, safe="") if region else "")
        body = self.get(path, params)
        data, total, meta = self.envelope(body)
        size = integer(meta.get("limit"), minimum=1)
        if len(data) > size or len(data) > total or (not data and page * size < total):
            raise SourceError("pagination_inconsistent")
        prepared = []
        for raw in data:
            company = raw.get("company")
            if not isinstance(company, dict):
                raise SourceError("search_schema_drift")
            try:
                vid = source_identity(
                    "trudvsem", raw.get("id"), company_code=company.get("companycode")
                ).source_id
            except ValueError:
                raise SourceError("invalid_source_id") from None
            prepared.append({**raw, "id": vid, "_vacancy_id": raw["id"]})
        return source_page(
            items=prepared,
            found=total,
            pages=math.ceil(total / size),
            page=page,
            per_page=size,
            has_more=(page + 1) * size < total,
            window_cap=None,
        )

    def detail(self, vacancy_id):
        try:
            company, vid = json.loads(vacancy_id)
        except (ValueError, TypeError):
            raise SourceError("invalid_source_id") from None
        data, total, _ = self.envelope(
            self.get(self.base + "/vacancy/" + quote(company, safe="") + "/" + quote(vid, safe=""))
        )
        if total == 0:
            raise SourceError("unavailable")
        if len(data) != 1 or total != 1:
            raise SourceError("detail_schema_drift")
        return trudvsem_record(data[0], vacancy_id)

    def inline_detail(self, item):
        # REST lists contain the complete vacancy object, including all text sections.
        raw = {k: v for k, v in item.items() if k != "_vacancy_id"}
        raw["id"] = item["_vacancy_id"]
        try:
            return trudvsem_record(raw, item["id"])
        except SourceError:
            return None  # The dedicated endpoint can still resolve an incomplete list row.


class SuperJobClient(ReadClient):
    source = "superjob"
    base = "https://api.superjob.ru/2.0/vacancies/"
    window_cap = 500
    supports_date_split = True

    def __init__(self, credentials, **kwargs):
        key = credentials.values.get("SUPERJOB_APP_KEY")
        if not key:
            raise SourceError("missing_application_credentials")
        super().__init__(headers={"X-Api-App-Id": key}, **kwargs)

    def search(self, query, start, end, page, per_page, *, area_ids=None):
        # Native search clamps large requests to 40 and can insert promoted rows
        # above the requested count. Their count must not change the page stride.
        size = min(per_page, 40)
        body = self.get(
            self.base,
            {
                "keyword": query,
                "page": page,
                "count": size,
                **(
                    {"date_published_from": int(datetime.fromisoformat(start).timestamp())}
                    if start
                    else {}
                ),
                **(
                    {"date_published_to": int(datetime.fromisoformat(end).timestamp())}
                    if end
                    else {}
                ),
                "order_field": "date",
                "order_direction": "desc",
                **({"town": self.options["town_id"]} if self.options.get("town_id") else {}),
            },
        )
        items = rows(body.get("objects"))
        total = integer(body.get("total"))
        more = body.get("more")
        if type(more) is not bool or len(items) > 100:
            raise SourceError("search_schema_drift")
        if not items and (more or page * size < min(total, self.window_cap)):
            raise SourceError("unexpected_empty_page")
        at_cap = (page + 1) * size >= self.window_cap
        return source_page(
            items=[{**raw, "id": numeric_id(raw.get("id"))} for raw in items],
            found=total,
            pages=math.ceil(min(total, self.window_cap) / size),
            page=page,
            per_page=size,
            has_more=more and not at_cap,
            window_cap=self.window_cap,
            truncated=more and at_cap or total > self.window_cap,
        )

    def detail(self, vacancy_id):
        numeric_id(vacancy_id)
        return superjob_record(self.get(self.base + vacancy_id + "/"), vacancy_id)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.current = None
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = dict(attrs)
            self.parts = []

    def handle_data(self, data):
        if self.current is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append((self.current, "".join(self.parts)))
            self.current = None


def habr_detail(html, expected):
    parser = Scripts()
    parser.feed(html)
    state = None
    jobs = []
    try:
        for attrs, raw in parser.scripts:
            if attrs.get("data-ssr-state") == "true":
                state = json.loads(raw)
            if attrs.get("type") == "application/ld+json":
                value = json.loads(raw)
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if isinstance(item, dict):
                        jobs.extend(item.get("@graph", []) if "@graph" in item else [item])
    except (ValueError, TypeError):
        raise SourceError("detail_parser_drift") from None
    if not isinstance(state, dict) or not isinstance(state.get("vacancy"), dict):
        raise SourceError("detail_parser_drift")
    vacancy = state["vacancy"]
    if str(vacancy.get("id")) != expected:
        raise SourceError("detail_identity_mismatch")
    jobs = [j for j in jobs if isinstance(j, dict) and j.get("@type") == "JobPosting"]
    # Closed cards can retain matching SSR identity but remove both the body
    # and JobPosting. An explicit tombstone is terminal, not a broken parser.
    if (
        vacancy.get("archived") is True
        and vacancy.get("href") == "/vacancies/" + expected
        and isinstance(vacancy.get("title"), str)
        and vacancy["title"].strip()
        and vacancy.get("description") in (None, "")
        and not jobs
    ):
        raise SourceError("unavailable")
    if len(jobs) != 1:
        raise SourceError("detail_parser_drift")
    return habr_record(vacancy, jobs[0], expected)


class HabrClient(ReadClient):
    source = "habr"
    base = "https://career.habr.com"

    def search(self, query, start, end, page, per_page, *, area_ids=None):
        params = {"q": query, "type": "all", "sort": "date", "page": page + 1, "per_page": per_page}
        if self.options.get("city_id"):
            params["city_id"] = self.options["city_id"]
        body = self.get(self.base + "/api/frontend/vacancies", params)
        items = rows(body.get("list"))
        meta = body.get("meta")
        if not isinstance(meta, dict):
            raise SourceError("search_schema_drift")
        total = integer(meta.get("totalResults"))
        size = integer(meta.get("perPage"), minimum=1)
        current = integer(meta.get("currentPage"), minimum=1)
        pages = integer(meta.get("totalPages"))
        if current != page + 1 or len(items) > size or len(items) > total:
            raise SourceError("pagination_mismatch")
        if total > 0 and (not items or pages < current):
            raise SourceError("unexpected_empty_page")
        return source_page(
            items=[{**raw, "id": numeric_id(raw.get("id"))} for raw in items],
            found=total,
            pages=pages,
            page=page,
            per_page=size,
            has_more=current < pages,
            window_cap=None,
            truncated=pages * size < total,
        )

    def detail(self, vacancy_id):
        numeric_id(vacancy_id)
        return habr_detail(self.request(self.base + "/vacancies/" + vacancy_id).text, vacancy_id)
