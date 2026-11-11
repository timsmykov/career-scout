"""Official HH read-only transport and lossless description normalization."""

from __future__ import annotations

import json
import math
import os
import random
import re
import tempfile
import time
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from career_scout.common import SourceError, digest, utcnow


class Credentials:
    def __init__(self, path: Path, token_path: Path | None = None):
        self.path = path
        self.token_path = token_path
        if token_path and token_path.resolve() == path.resolve():
            raise SourceError("token_cache_overlaps_credentials")
        self.values = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    self.values[key.strip()] = value.strip().strip("\"'")
        if token_path and token_path.exists():
            try:
                cache = json.loads(token_path.read_text())
                identity = digest(self.values.get("HH_CLIENT_ID"))
                if cache["application_hash"] == identity:
                    token = cache["access_token"]
                    if not isinstance(token, str) or not re.fullmatch(
                        r"[A-Za-z0-9._~+/=-]+", token
                    ):
                        raise ValueError
                    self.values["HH_ACCESS_TOKEN"] = token
            except (ValueError, KeyError, TypeError):
                raise SourceError("invalid_token_cache") from None

    def save_token(self, token: str) -> None:
        if self.token_path:
            self.token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = {
                "application_hash": digest(self.values.get("HH_CLIENT_ID")),
                "access_token": token,
            }
            fd, name = tempfile.mkstemp(prefix=".token.", dir=self.token_path.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(data, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.token_path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            self.values["HH_ACCESS_TOKEN"] = token
            return
        # Preserve unrelated credentials/comments, including the SuperJob key.
        lines = self.path.read_text().splitlines() if self.path.exists() else []
        indices = [i for i, line in enumerate(lines) if line.startswith("HH_ACCESS_TOKEN=")]
        if len(indices) > 1:
            raise SourceError("duplicate_token_variable")
        entry = "HH_ACCESS_TOKEN=" + token
        if indices:
            lines[indices[0]] = entry
        else:
            lines.append(entry)
        fd, name = tempfile.mkstemp(prefix=".env.", dir=self.path.resolve().parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write("\n".join(lines) + "\n")
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.values["HH_ACCESS_TOKEN"] = token


class Page(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[dict]
    found: StrictInt = Field(ge=0)
    pages: StrictInt = Field(ge=0)
    page: StrictInt = Field(ge=0)
    per_page: StrictInt = Field(ge=1, le=100)


class DescriptionParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3"}:
            self.parts.append("\n" + ("- " if tag == "li" else ""))

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "ul", "ol"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def description_text(html: str) -> str:
    parser = DescriptionParser()
    parser.feed(html)
    parser.close()
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def meaningful(payload: dict) -> dict:
    # Explicit fields: viewer counters and search snippets do not change content versions.
    keys = (
        "name",
        "description",
        "salary",
        "salary_range",
        "experience",
        "area",
        "address",
        "schedule",
        "employment",
        "employment_form",
        "work_format",
        "work_formats",
        "working_hours",
        "work_schedule_by_days",
        "key_skills",
        "professional_roles",
        "languages",
        "archived",
        "accept_temporary",
        "accept_handicapped",
        "night_shifts",
    )
    value = {key: payload.get(key) for key in keys}
    for key in ("salary", "salary_range", "experience", "area", "schedule", "employment"):
        if value[key] is not None and not isinstance(value[key], dict):
            raise SourceError("invalid_conditions_schema")
    if payload.get("archived") is not None and type(payload["archived"]) is not bool:
        raise SourceError("invalid_conditions_schema")
    employer = payload.get("employer") or {}
    if not isinstance(employer, dict):
        raise SourceError("invalid_employer_schema")
    if employer.get("name") is not None and not isinstance(employer["name"], str):
        raise SourceError("invalid_employer_schema")
    value["employer"] = {key: employer.get(key) for key in ("id", "name")}
    return value


def listing_hash(payload: dict) -> str:
    value = meaningful(payload)
    # Description belongs to detail, not a search snippet.
    value.pop("description")
    return digest(value)


def normalize(payload: dict, expected_id: str) -> dict:
    if str(payload.get("id")) != expected_id:
        raise SourceError("detail_identity_mismatch")
    if not isinstance(payload.get("name"), str) or not payload["name"].strip():
        raise SourceError("detail_schema_drift")
    html = payload.get("description")
    if not isinstance(html, str) or not description_text(html):
        raise SourceError("detail_missing_description")
    # Source timestamps are preserved, never manufactured from the download clock.
    content = meaningful(payload)
    text = description_text(html)
    return {
        "schema_version": 1,
        "source": "hh",
        "source_id": expected_id,
        "url": f"https://hh.ru/vacancy/{expected_id}",
        "title": payload["name"],
        "company": (payload.get("employer") or {}).get("name"),
        "description": text,
        "description_html": html,
        "source_payload": payload,
        "conditions": content,
        "published_at": payload.get("published_at"),
        "created_at": payload.get("created_at"),
        "source_updated_at": payload.get("updated_at"),
        "detail_fetched_at": utcnow(),
        "normalizer_version": "hh-1",
        "content_hash": digest({"normalizer": "hh-1", "content": content}),
        "detail_status": "archived" if payload.get("archived") else "full",
    }


class HHClient:
    def __init__(
        self,
        credentials: Credentials,
        *,
        max_requests=50,
        retries=2,
        interval=1.0,
        timeout=25.0,
        client=None,
        sleep=time.sleep,
        clock=time.time,
    ):
        if not credentials.values.get("HH_USER_AGENT"):
            raise SourceError("missing_user_agent")
        self.credentials = credentials
        self.limit = max_requests
        self.retries = retries
        self.interval = interval
        self.sleep = sleep
        self.clock = clock
        self.used = 0
        self.last_request: float | None = None
        self.auth_attempted = False
        self.on_request = lambda: None
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def _reserve(self):
        if self.used >= self.limit:
            raise SourceError("request_budget_exhausted")
        if self.last_request is not None:
            self.sleep(max(0, self.interval - (self.clock() - self.last_request)))
        self.on_request()
        self.used += 1
        self.last_request = self.clock()

    def _acquire(self):
        if self.auth_attempted:
            raise SourceError("auth_required")
        self.auth_attempted = True
        values = self.credentials.values
        if not values.get("HH_CLIENT_ID") or not values.get("HH_CLIENT_SECRET"):
            raise SourceError("missing_application_credentials")
        self._reserve()
        try:
            response = self.client.post(
                "https://api.hh.ru/token",
                headers={"User-Agent": values["HH_USER_AGENT"]},
                data={
                    "grant_type": "client_credentials",
                    "client_id": values["HH_CLIENT_ID"],
                    "client_secret": values["HH_CLIENT_SECRET"],
                },
            )
        except httpx.TransportError:
            raise SourceError("auth_transport_error") from None
        if response.status_code != 200:
            raise SourceError("auth_required")
        try:
            token = response.json()["access_token"]
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SourceError("invalid_token_response") from None
        self.credentials.save_token(token)

    def get(self, path: str, params=None) -> dict:
        if not self.credentials.values.get("HH_ACCESS_TOKEN"):
            self._acquire()
        transient_attempts = 0
        while True:
            self._reserve()
            try:
                response = self.client.get(
                    "https://api.hh.ru" + path,
                    params=params,
                    headers={
                        "User-Agent": self.credentials.values["HH_USER_AGENT"],
                        "Authorization": "Bearer " + self.credentials.values["HH_ACCESS_TOKEN"],
                    },
                )
            except httpx.TransportError:
                if transient_attempts >= self.retries:
                    raise SourceError("transport_error") from None
                self.sleep(2**transient_attempts + random.uniform(0, 0.25))
                transient_attempts += 1
                continue
            status = response.status_code
            if status == 401:
                self._acquire()
                continue
            if status == 403:
                raise SourceError("access_denied")
            if status == 429:
                retry_after = response.headers.get("Retry-After", "60")
                try:
                    seconds = float(retry_after)
                except ValueError:
                    try:
                        seconds = max(
                            0, parsedate_to_datetime(retry_after).timestamp() - self.clock()
                        )
                    except (ValueError, TypeError, OverflowError):
                        seconds = 60
                if not math.isfinite(seconds):
                    seconds = 60
                seconds = max(0, seconds)
                raise SourceError("rate_limited", self.clock() + seconds)
            if status >= 500:
                if transient_attempts >= self.retries:
                    raise SourceError("server_error")
                self.sleep(2**transient_attempts + random.uniform(0, 0.25))
                transient_attempts += 1
                continue
            if status in {404, 410}:
                raise SourceError("unavailable")
            if status != 200:
                raise SourceError("unexpected_http_status")
            try:
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError
                return body
            except (ValueError, TypeError):
                raise SourceError("invalid_json") from None

    def search(
        self, query: str, start: str, end: str, page: int, per_page: int, *, area_ids=None
    ) -> Page:
        params = {
            "text": query,
            "date_from": start,
            "date_to": end,
            "page": page,
            "per_page": per_page,
            "order_by": "publication_time",
            "no_magic": "true",
        }
        if area_ids:
            params["area"] = area_ids
        body = self.get("/vacancies", params)
        try:
            result = Page.model_validate(body)
        except ValidationError:
            raise SourceError("search_schema_drift") from None
        if result.page != page or result.per_page != per_page:
            raise SourceError("pagination_mismatch")
        expected_pages = (min(result.found, 2000) + per_page - 1) // per_page
        if (
            len(result.items) > per_page
            or len(result.items) > result.found
            or (result.found and result.pages != expected_pages)
            or (result.items and result.page >= result.pages)
        ):
            raise SourceError("pagination_inconsistent")
        if not result.items and result.found > 0:
            raise SourceError("unexpected_empty_page")
        return result

    def detail(self, vacancy_id: str) -> dict:
        return normalize(self.get("/vacancies/" + vacancy_id), vacancy_id)
