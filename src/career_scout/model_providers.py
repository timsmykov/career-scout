"""Data-only model transports. No tools, hidden retries, or stored credentials."""

import json
import os
import threading
import time
from pathlib import Path

import httpx

from career_scout.batch_worker import CodexBatchWorker
from career_scout.common import strict_json
from career_scout.evaluation import APISettings, EvaluationError

# Fixed origins prevent key forwarding through model-generated URLs or redirects.
PROVIDERS = {
    "openai": ("https://api.openai.com/v1/responses", "OPENAI_API_KEY", "responses"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY", "messages"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/models/",
        "GEMINI_API_KEY",
        "gemini",
    ),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", "chat"),
    "deepseek": ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY", "chat"),
    "mistral": ("https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY", "chat"),
    "xai": ("https://api.x.ai/v1/chat/completions", "XAI_API_KEY", "chat"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "chat"),
    "together": ("https://api.together.xyz/v1/chat/completions", "TOGETHER_API_KEY", "chat"),
    "fireworks": (
        "https://api.fireworks.ai/inference/v1/chat/completions",
        "FIREWORKS_API_KEY",
        "chat",
    ),
}


def provider_catalog():
    return {
        "api": [
            {"provider": p, "key_env": key, "protocol": protocol, "endpoint": url}
            for p, (url, key, protocol) in PROVIDERS.items()
        ],
        "subscriptions": ["codex_subscription", "claude_subscription"],
        "live_verified": False,
    }


def credential(config, env_path=None):
    name = (config.api or APISettings()).key_env or PROVIDERS[config.provider][1]
    # Same literal env-file parser as platform credentials; no sourcing/evaluation.
    value = None
    if env_path is not None:
        from career_scout.hh import Credentials

        value = Credentials(Path(env_path)).values.get(name)
    value = value or os.environ.get(name)
    if not value or not value.strip() or any(c.isspace() for c in value):
        raise EvaluationError("model_api_key_missing_or_invalid", fatal=True)
    return value


def normalized_usage(data, protocol):
    raw = data.get("usageMetadata" if protocol == "gemini" else "usage")
    required = {
        "gemini": ("promptTokenCount", "candidatesTokenCount"),
        "chat": ("prompt_tokens", "completion_tokens"),
    }.get(protocol, ("input_tokens", "output_tokens"))
    if not isinstance(raw, dict) or any(type(raw.get(k)) is not int for k in required):
        return None
    try:
        values = usage_values(data, protocol)
    except (TypeError, AttributeError):
        return None
    if any(type(v) is not int or v < 0 for v in values.values()):
        return None
    return values


def usage_values(data, protocol):
    if protocol == "gemini":
        raw = data.get("usageMetadata") or {}
        return {
            "input_tokens": raw.get("promptTokenCount", 0),
            "output_tokens": raw.get("candidatesTokenCount", 0) + raw.get("thoughtsTokenCount", 0),
            "cached_input_tokens": raw.get("cachedContentTokenCount", 0),
        }
    raw = data.get("usage") or {}
    if protocol == "chat":
        return {
            "input_tokens": raw.get("prompt_tokens", 0),
            "output_tokens": raw.get("completion_tokens", 0),
            "cached_input_tokens": (raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        }
    if protocol == "messages":
        return {
            "input_tokens": sum(
                raw.get(k, 0)
                for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            ),
            "output_tokens": raw.get("output_tokens", 0),
            "cached_input_tokens": raw.get("cache_read_input_tokens", 0),
        }
    return {
        "input_tokens": raw.get("input_tokens", 0),
        "output_tokens": raw.get("output_tokens", 0),
        "cached_input_tokens": (raw.get("input_tokens_details") or {}).get("cached_tokens", 0),
    }


class APIBatchWorker(CodexBatchWorker):
    """Reuse the exact batch/evidence validation used for Codex."""

    def __init__(self, config, *, env_path=None, client=None):
        self.config = config
        self.env_path = env_path
        self.client = client
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def request(self, prompt, schema):
        import re

        settings = self.config.api or APISettings()
        url, _, protocol = PROVIDERS[self.config.provider]
        headers = {"Content-Type": "application/json"}
        key = credential(self.config, self.env_path)
        schema_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "vacancy_batch",
                "strict": True,
                "schema": schema,
            },
        }
        fmt = schema_format if settings.output_mode == "schema" else {"type": "json_object"}
        # Provider JSON modes differ; local evidence/verdict validation never changes.
        if settings.output_mode == "json":
            prompt += "\n\nReturn only a JSON object satisfying this JSON Schema:\n" + json.dumps(
                schema
            )
        body = {"model": self.config.model}
        if protocol == "responses":
            headers["Authorization"] = "Bearer " + key
            body.update(input=prompt, store=False, max_output_tokens=settings.max_output_tokens)
            body["text"] = {
                "format": (
                    {"type": "json_schema", **schema_format["json_schema"]}
                    if settings.output_mode == "schema"
                    else fmt
                )
            }
            if settings.reasoning_effort:
                body["reasoning"] = {"effort": settings.reasoning_effort}
        elif protocol == "messages":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            body.update(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.max_output_tokens,
            )
        elif protocol == "gemini":
            # A model ID must not replace the fixed endpoint origin/path.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", self.config.model):
                raise EvaluationError("invalid_gemini_model_id", fatal=True)
            url += self.config.model + ":generateContent"
            headers["x-goog-api-key"] = key
            generation = {
                "responseMimeType": "application/json",
                "maxOutputTokens": settings.max_output_tokens,
            }
            if settings.output_mode == "schema":
                generation["responseJsonSchema"] = schema
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": generation,
            }
        else:
            headers["Authorization"] = "Bearer " + key
            body.update(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.max_output_tokens,
                response_format=fmt,
                stream=False,
            )
            if settings.reasoning_effort:
                body["reasoning"] = {"effort": settings.reasoning_effort}
            if self.config.provider == "openrouter":
                body["provider"] = {"require_parameters": True, "allow_fallbacks": False}
        return url, headers, body, protocol

    def invoke(self, prompt, schema, *, max_input_characters=None):
        limit = (
            self.config.max_input_characters
            if max_input_characters is None
            else max_input_characters
        )
        if type(limit) is not int or not 100 <= limit <= 2_000_000:
            raise EvaluationError("invalid_transport_context_limit", fatal=True)
        # Count the actual serialized schema added to the prompt (including ASCII escaping).
        if len(prompt) + len(json.dumps(schema)) + 100 > limit:
            raise EvaluationError("input_exceeds_configured_context")
        if self._cancelled.is_set():
            raise EvaluationError("model_cancelled", fatal=True)
        url, headers, body, protocol = self.request(prompt, schema)
        owned = self.client is None
        client = self.client or httpx.Client(
            timeout=self.config.timeout_seconds, follow_redirects=False
        )
        started = time.monotonic()
        try:
            with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=self.config.timeout_seconds,
                follow_redirects=False,
            ) as response:
                code = response.status_code
                if code != 200:
                    if code >= 500 or code in {408, 504}:
                        raise EvaluationError("model_outcome_unknown", fatal=True)
                    error = {
                        401: "model_authentication_failed",
                        403: "model_access_denied",
                        429: "model_rate_limited",
                    }.get(code, "model_request_rejected")
                    raise EvaluationError(error, fatal=True)
                chunks, length = [], 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if (
                        length > 16_000_000
                        or self._cancelled.is_set()
                        or time.monotonic() - started > self.config.timeout_seconds
                    ):
                        raise EvaluationError("model_outcome_unknown", fatal=True)
                    chunks.append(chunk)
                raw = b"".join(chunks)
        except httpx.RequestError:
            # A timeout/connection loss can occur AFTER the paid request was accepted.
            raise EvaluationError("model_outcome_unknown", fatal=True) from None
        finally:
            if owned:
                client.close()
        usage = None
        try:
            data = strict_json(raw)
            usage = normalized_usage(data, protocol)
            text = response_text(data, protocol)
            wire = strict_json(text)
            if not isinstance(wire, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise EvaluationError("model_invalid_response", usage=usage) from None
        return wire, usage


def response_text(data, protocol):
    if protocol == "responses":
        if data.get("status") != "completed":
            raise ValueError
        outputs = data["output"]
        if any(x.get("type") != "message" for x in outputs if x.get("type") != "reasoning"):
            raise ValueError
        parts = [p for item in outputs if item.get("type") == "message" for p in item["content"]]
        if any(p.get("type") != "output_text" for p in parts):
            raise ValueError
        return "".join(p["text"] for p in parts)
    if protocol == "messages":
        if data.get("stop_reason") != "end_turn" or any(
            p.get("type") != "text" for p in data["content"]
        ):
            raise ValueError
        return "".join(p["text"] for p in data["content"])
    if protocol == "gemini":
        candidates = data["candidates"]
        if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise ValueError
        parts = candidates[0]["content"]["parts"]
        if any("text" not in p for p in parts):
            raise ValueError
        return "".join(p["text"] for p in parts if not p.get("thought"))
    choices = data["choices"]
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        raise ValueError
    message = choices[0]["message"]
    if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
        raise ValueError
    return message["content"]


def batch_worker(config, *, env_path=None):
    if config.provider == "codex_subscription":
        return CodexBatchWorker(config)
    if config.provider == "claude_subscription":
        from career_scout.claude_worker import ClaudeBatchWorker

        return ClaudeBatchWorker(config)
    return APIBatchWorker(config, env_path=env_path)
