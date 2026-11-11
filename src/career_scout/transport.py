"""Bounded read-only HTTP for public and application-key sources. No auth bodies logged."""

import math
import time
from email.utils import parsedate_to_datetime

import httpx

from career_scout.common import SourceError


class ReadClient:
    source: str
    window_cap = None
    supports_date_split = False

    def __init__(
        self,
        *,
        headers=None,
        max_requests=50,
        retries=2,
        interval=1.0,
        timeout=30.0,
        client=None,
        sleep=time.sleep,
        clock=time.time,
        options=None,
    ):
        self.headers = headers or {}
        self.limit = max_requests
        self.retries = retries
        self.interval = interval
        self.sleep = sleep
        self.clock = clock
        self.used = 0
        self.last_request = None
        self.on_request = lambda: None
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self.owns_client = client is None
        self.options = options or {}

    def close(self):
        if self.owns_client:
            self.client.close()

    def retry_at(self, response):
        value = response.headers.get("Retry-After", "60")
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - self.clock()
            except (ValueError, TypeError, OverflowError):
                seconds = 60
        return self.clock() + (max(0, seconds) if math.isfinite(seconds) else 60)

    def request(self, url, params=None):
        for attempt in range(self.retries + 1):
            if self.used >= self.limit:
                raise SourceError("request_budget_exhausted")
            if self.last_request is not None:
                self.sleep(max(0, self.interval - (self.clock() - self.last_request)))
            self.on_request()
            self.used += 1
            self.last_request = self.clock()
            try:
                response = self.client.get(url, params=params, headers=self.headers)
            except httpx.TransportError:
                if attempt == self.retries:
                    raise SourceError("transport_error") from None
                self.sleep(2**attempt)
                continue
            if response.status_code == 401:
                raise SourceError("auth_required")
            if response.status_code == 403:
                raise SourceError("access_denied")
            if response.status_code == 429 or (
                self.source == "superjob" and response.status_code == 503
            ):
                raise SourceError("rate_limited", self.retry_at(response))
            if response.status_code in (404, 410):
                raise SourceError("unavailable")
            if response.status_code >= 500:
                if attempt == self.retries:
                    raise SourceError("server_error")
                self.sleep(2**attempt)
                continue
            if response.status_code != 200:
                raise SourceError("unexpected_http_status")
            return response
        raise AssertionError("unreachable")

    def get(self, url, params=None):
        response = self.request(url, params)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise SourceError("invalid_json") from None
        error = result.get("error")
        if error:
            code = error.get("code") if isinstance(error, dict) else None
            if code in (401, 403):
                raise SourceError("auth_required" if code == 401 else "access_denied")
            if code in (429, 503):
                raise SourceError("rate_limited", self.retry_at(response))
            raise SourceError("source_error_envelope")
        return result
