"""Source-independent errors, clock and stable hashing."""

import hashlib
import json
from datetime import UTC, datetime


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: object) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class SourceError(Exception):
    """Only fixed safe error codes cross the CLI boundary."""

    def __init__(self, code: str, retry_at: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at
