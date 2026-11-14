"""Source-independent errors, clock and stable hashing."""

import hashlib
import json
import math
from datetime import UTC, datetime


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: object) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def strict_json(text: str):
    """Reject ambiguous duplicate keys and non-JSON numeric constants at I/O boundaries."""

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("invalid_json_constant")

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("invalid_json_number")
        return result

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


class SourceError(Exception):
    """Only fixed safe error codes cross the CLI boundary."""

    def __init__(self, code: str, retry_at: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at
