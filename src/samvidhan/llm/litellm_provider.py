"""LiteLLM-backed provider (ADR-0009). The only module that imports litellm.

API keys come from Settings (loaded from `.env`), passed per call; litellm never reads them from
`os.environ`, and they are never logged.
"""

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import litellm
from litellm import exceptions as llm_errors

from samvidhan.core.config import Settings
from samvidhan.core.logging import get_logger
from samvidhan.llm.types import Completion, ProviderError, ProviderRequest, provider_of

log = get_logger(__name__)

litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" banners on stdout

# Most specific first: Timeout subclasses APIConnectionError, ContextWindowExceededError
# subclasses BadRequestError.
_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (llm_errors.Timeout, "timeout"),
    (llm_errors.RateLimitError, "rate_limited"),
    (llm_errors.AuthenticationError, "auth"),
    (llm_errors.PermissionDeniedError, "auth"),
    (llm_errors.ContextWindowExceededError, "context_window"),
    (llm_errors.NotFoundError, "not_found"),
    (llm_errors.BadRequestError, "bad_request"),
    (llm_errors.UnprocessableEntityError, "bad_request"),
    (llm_errors.APIConnectionError, "connection"),
    (llm_errors.InternalServerError, "provider_5xx"),
    (llm_errors.ServiceUnavailableError, "provider_5xx"),
    (llm_errors.BadGatewayError, "provider_5xx"),
)


def error_code(exc: Exception) -> str:
    for exc_type, code in _ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return "provider_5xx"
    return "provider_error"


class LiteLLMProvider:
    def __init__(self, settings: Settings) -> None:
        self._keys = {
            "groq": settings.groq_api_key.get_secret_value(),
            "gemini": settings.gemini_api_key.get_secret_value(),
        }

    def _kwargs(self, request: ProviderRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": list(request.messages),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "timeout": request.timeout_s,
            "num_retries": 0,  # LLMClient retries; litellm must not retry on its own
            "drop_params": True,  # a param a provider lacks is dropped, not a failed call
        }
        key = self._keys.get(provider_of(request.model))
        if key:
            kwargs["api_key"] = key
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    async def complete(self, request: ProviderRequest) -> Completion:
        try:
            response = await litellm.acompletion(**self._kwargs(request))
        except Exception as exc:
            raise ProviderError(error_code(exc), type(exc).__name__) from exc
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        return Completion(
            text=choice.message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            finish_reason=choice.finish_reason,
            cost_usd=_cost(response),
        )

    async def stream(self, request: ProviderRequest) -> AsyncIterator[str | Completion]:
        kwargs = self._kwargs(request) | {
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        parts: list[str] = []
        finish_reason: str | None = None
        usage: Any = None
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                usage = getattr(chunk, "usage", None) or usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta.content if choice.delta else None
                if delta:
                    parts.append(delta)
                    yield delta
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(error_code(exc), type(exc).__name__) from exc
        yield Completion(
            text="".join(parts),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            finish_reason=finish_reason,
        )


def _cost(response: Any) -> Decimal:
    """LiteLLM price map cost; 0 for free tiers or models missing from the map."""
    try:
        return Decimal(str(round(litellm.completion_cost(completion_response=response), 6)))
    except Exception:  # unknown model in the price map is normal on free tiers
        return Decimal(0)
