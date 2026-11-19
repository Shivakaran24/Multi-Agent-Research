"""Resilient model calls: retry rate limits and malformed structured output.

Kept free of heavy imports so it can be unit-tested without the LLM libraries.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from typing import Any

MAX_RATE_LIMIT_RETRIES = 5
MAX_JSON_RETRIES = 2


def _status_code(exc: Exception) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code


def is_invalid_json_error(exc: Exception) -> bool:
    """Groq answers HTTP 400 `json_validate_failed` when the model's JSON is malformed."""
    text = f"{getattr(exc, 'body', '')} {exc}"
    return "json_validate_failed" in text or exc.__class__.__name__ == "OutputParserException"


def failed_generation(exc: Exception) -> str | None:
    """The raw text the model produced, as reported in the provider's error."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        for holder in (body, body.get("error")):
            if isinstance(holder, dict) and isinstance(holder.get("failed_generation"), str):
                return holder["failed_generation"]
    match = re.search(r"'failed_generation': (['\"])(.*)\1\}\}", str(exc), re.S)
    if match:
        try:
            return json.loads('"' + match.group(2).replace('"', '\\"').replace("\\'", "'") + '"')
        except ValueError:
            return match.group(2)
    return None


def salvage_single_string_field(raw: str) -> dict[str, str] | None:
    """Recover `{"field": "text..."}` whose closing quote or escapes were malformed."""
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    match = re.match(r'\s*\{\s*"(\w+)"\s*:\s*"(.*)$', raw, re.S)
    if not match:
        return None
    value = re.sub(r'\s*\\*"?\s*\}+\s*$', "", match.group(2))
    try:
        value = json.loads('"' + value + '"')
    except ValueError:
        value = value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    value = value.strip()
    return {match.group(1): value} if value else None


def invoke_with_retry(
    runnable: Any,
    messages: list[Any],
    schema: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call `runnable.invoke(messages)`.

    - HTTP 429: wait as the provider asks, with backoff.
    - Malformed JSON from the model: regenerate (up to MAX_JSON_RETRIES times). If it keeps
      failing and `schema` has a single text field, recover the text from the error instead of
      failing the whole research run.
    """
    rate_attempts = 0
    json_attempts = 0
    while True:
        try:
            return runnable.invoke(messages)
        except Exception as exc:
            if _status_code(exc) == 429 and rate_attempts < MAX_RATE_LIMIT_RETRIES:
                sleep(_rate_limit_wait(exc, rate_attempts))
                rate_attempts += 1
                continue
            if is_invalid_json_error(exc):
                if json_attempts < MAX_JSON_RETRIES:
                    json_attempts += 1
                    continue
                if schema is not None and (raw := failed_generation(exc)):
                    recovered = salvage_single_string_field(raw)
                    if recovered:
                        try:
                            return schema.model_validate(recovered)
                        except Exception:
                            pass
            raise


def _rate_limit_wait(exc: Exception, attempt: int) -> float:
    retry_after = None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    try:
        retry_after = float(headers.get("retry-after", "")) or None
    except (TypeError, ValueError):
        pass
    if retry_after is None:
        match = re.search(r"try again in ([0-9.]+)\s*(ms|milliseconds?|s|seconds?)", str(exc), re.I)
        if match:
            retry_after = float(match.group(1))
            if match.group(2).lower().startswith("m"):
                retry_after /= 1_000
    wait_seconds = min(30.0, (retry_after or 1.0) * (2**attempt))
    return wait_seconds + random.uniform(0, min(0.25, wait_seconds * 0.1))
