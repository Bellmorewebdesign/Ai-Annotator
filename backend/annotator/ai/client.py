"""Thin, testable wrapper around the Anthropic SDK for structured JSON calls.

Responsibilities: structured output via ``output_config.format``, streaming (no HTTP
timeouts on long outputs), prompt caching markers, refusal fallback, retries with
backoff and ``retry-after``, a process-wide concurrency limit, and usage/cost
accounting. It never logs prompt or response content.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import anthropic

from ..config import Settings
from .pricing import estimate_cost

log = logging.getLogger("annotator.ai")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIError(Exception):
    """Base error. ``user_message`` is safe to show to end users."""

    retryable = False

    def __init__(self, user_message: str, code: str = "ai_error"):
        super().__init__(user_message)
        self.user_message = user_message
        self.code = code


class AIConfigError(AIError):
    pass


class AIRefusal(AIError):
    pass


class AITransientError(AIError):
    retryable = True


@dataclass
class CallRecord:
    purpose: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None
    duration_s: float = 0.0
    attempts: int = 1
    stop_reason: str = ""
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredResult:
    data: dict[str, Any]
    record: CallRecord
    raw_text: str = field(default="", repr=False)


class StructuredClient(Protocol):
    def structured(self, *, purpose: str, system: str, blocks: list[dict[str, Any]], schema: dict[str, Any],
                   model: str, effort: str | None, max_tokens: int) -> StructuredResult: ...


class ClaudeClient:
    """Real client. One instance per worker process."""

    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise AIConfigError("The AI service is not configured. Add ANTHROPIC_API_KEY to the .env file.",
                                "ai_not_configured")
        self.settings = settings
        # Retries are handled here so attempts and waits are visible in job metrics.
        self.sdk = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0, timeout=900.0)
        self._sem = threading.BoundedSemaphore(max(1, settings.ai_concurrency))

    def structured(self, *, purpose: str, system: str, blocks: list[dict[str, Any]], schema: dict[str, Any],
                   model: str, effort: str | None, max_tokens: int) -> StructuredResult:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": blocks}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if effort:
            params["output_config"]["effort"] = effort
        use_fallback = self.settings.refusal_fallback and "haiku" not in model

        attempts = 0
        start = time.monotonic()
        while True:
            attempts += 1
            try:
                with self._sem:
                    message = self._call(params, use_fallback)
                break
            except AIError as exc:
                if not exc.retryable or attempts >= self.settings.ai_max_attempts:
                    raise
                self._sleep(attempts, None)
            except anthropic.RateLimitError as exc:
                if attempts >= self.settings.ai_max_attempts:
                    raise AITransientError("The AI service is busy right now. Please try again in a few minutes.",
                                           "ai_rate_limited") from exc
                self._sleep(attempts, _retry_after(exc))
            except (anthropic.APIConnectionError, anthropic.InternalServerError, anthropic.OverloadedError,
                    anthropic.ServiceUnavailableError) as exc:
                if attempts >= self.settings.ai_max_attempts:
                    raise AITransientError("The AI service could not be reached. Please try again shortly.",
                                           "ai_unavailable") from exc
                self._sleep(attempts, _retry_after(exc) if isinstance(exc, anthropic.APIStatusError) else None)
            except anthropic.AuthenticationError as exc:
                raise AIConfigError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.",
                                    "ai_auth") from exc
            except anthropic.PermissionDeniedError as exc:
                raise AIConfigError("The Anthropic API key does not have access to this model.", "ai_permission") from exc
            except anthropic.NotFoundError as exc:
                raise AIConfigError(f"The configured model '{model}' was not found. Check ANNOTATOR_MODEL.",
                                    "ai_model_not_found") from exc
            except anthropic.RequestTooLargeError as exc:
                raise AIError("This document is too large for a single AI request.", "ai_too_large") from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and attempts < self.settings.ai_max_attempts:
                    self._sleep(attempts, None)
                    continue
                log.error("AI request failed: status=%s purpose=%s", exc.status_code, purpose)
                raise AIError("The AI request failed. Please try again.", "ai_request_failed") from exc

        duration = time.monotonic() - start
        usage = message.usage
        served_model = getattr(message, "model", None) or model
        record = CallRecord(
            purpose=purpose, model=served_model,
            input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            duration_s=round(duration, 2), attempts=attempts, stop_reason=str(message.stop_reason or ""),
            request_id=str(getattr(message, "_request_id", "") or ""),
        )
        record.cost_usd = estimate_cost(served_model, record.input_tokens, record.output_tokens,
                                        record.cache_write_tokens, record.cache_read_tokens,
                                        self.settings.price_input_per_mtok, self.settings.price_output_per_mtok)
        log.info("AI call purpose=%s model=%s in=%s out=%s cache_r=%s cache_w=%s %.1fs attempts=%s",
                 purpose, served_model, record.input_tokens, record.output_tokens, record.cache_read_tokens,
                 record.cache_write_tokens, duration, attempts)

        if message.stop_reason == "refusal":
            raise AIRefusal("The AI declined to process this document.", "ai_refusal")
        if message.stop_reason == "max_tokens":
            raise AIError("The AI response was cut off because it was too long.", "ai_truncated")
        text = next((b.text for b in message.content if getattr(b, "type", "") == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIError("The AI returned an unreadable response.", "ai_bad_json") from exc
        return StructuredResult(data=data, record=record, raw_text=text)

    def _call(self, params: dict[str, Any], use_fallback: bool):
        if use_fallback:
            with self.sdk.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **params) as stream:
                return stream.get_final_message()
        with self.sdk.messages.stream(**params) as stream:
            return stream.get_final_message()

    @staticmethod
    def _sleep(attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else min(60.0, (2 ** attempt) + random.uniform(0, 1))
        time.sleep(max(0.5, min(delay, 120.0)))


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value else None
    except (AttributeError, ValueError):
        return None


def make_client(settings: Settings) -> StructuredClient:
    if settings.fake_ai_enabled:
        from .fake import FakeClaude

        return FakeClaude()
    return ClaudeClient(settings)
