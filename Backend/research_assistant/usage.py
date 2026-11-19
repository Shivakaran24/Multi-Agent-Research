"""Count model tokens for one research run via a LangChain callback."""

from __future__ import annotations

import threading
from typing import Any

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # keeps the module importable where langchain is stubbed (tests)
    BaseCallbackHandler = object  # type: ignore[assignment,misc]


class UsageTracker(BaseCallbackHandler):  # type: ignore[misc]
    """Add up input and output tokens from every model call made during a run."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self._lock = threading.Lock()

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        found_in = found_out = 0
        counted = False
        for generations in getattr(response, "generations", None) or []:
            for generation in generations:
                meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if meta:
                    found_in += int(meta.get("input_tokens") or 0)
                    found_out += int(meta.get("output_tokens") or 0)
                    counted = True
        if not counted:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            found_in = int(usage.get("prompt_tokens") or 0)
            found_out = int(usage.get("completion_tokens") or 0)
        with self._lock:
            self.input_tokens += found_in
            self.output_tokens += found_out
