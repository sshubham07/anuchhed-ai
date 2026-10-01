"""LiteLLMProvider: error codes, per-provider keys, response parsing — no network."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from litellm import exceptions as llm_errors
from pydantic import SecretStr

from samvidhan.core.config import Settings
from samvidhan.llm import litellm_provider
from samvidhan.llm.litellm_provider import LiteLLMProvider, error_code
from samvidhan.llm.types import Completion, ProviderError, ProviderRequest


def request(model: str = "groq/llama-3.1-8b-instant", json_mode: bool = False) -> ProviderRequest:
    return ProviderRequest(
        purpose="router",
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=10,
        temperature=0.0,
        timeout_s=5,
        json_mode=json_mode,
    )


def _err(cls: type[Exception], **kwargs: Any) -> Exception:
    return cls(message="boom", model="m", llm_provider="groq", **kwargs)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_err(llm_errors.Timeout), "timeout"),
        (_err(llm_errors.RateLimitError), "rate_limited"),
        (_err(llm_errors.AuthenticationError), "auth"),
        (_err(llm_errors.NotFoundError), "not_found"),
        (_err(llm_errors.ServiceUnavailableError), "provider_5xx"),
        (_err(llm_errors.APIConnectionError), "connection"),
        (ValueError("weird"), "provider_error"),
    ],
)
def test_error_codes(exc: Exception, code: str) -> None:
    assert error_code(exc) == code


@pytest.fixture
def provider(settings: Settings) -> LiteLLMProvider:
    return LiteLLMProvider(
        settings.model_copy(
            update={"groq_api_key": SecretStr("gsk-test"), "gemini_api_key": SecretStr("")}
        )
    )


async def test_complete_passes_key_json_mode_and_parses(
    monkeypatch: pytest.MonkeyPatch, provider: LiteLLMProvider
) -> None:
    seen: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
        )

    monkeypatch.setattr(litellm_provider.litellm, "acompletion", fake_acompletion)
    completion = await provider.complete(request(json_mode=True))
    assert completion == Completion("{}", 12, 3, "stop", completion.cost_usd)
    assert seen["api_key"] == "gsk-test" and seen["num_retries"] == 0
    assert seen["response_format"] == {"type": "json_object"}

    seen.clear()
    await provider.complete(request(model="gemini/flash"))
    assert "api_key" not in seen and "response_format" not in seen  # unset key: not passed
    assert "reasoning_effort" not in seen

    seen.clear()
    await provider.complete(request(model="groq/qwen/qwen3.8-27b"))
    assert seen["reasoning_effort"] == "none"  # from MODEL_REASONING_EFFORT


async def test_provider_errors_are_wrapped(
    monkeypatch: pytest.MonkeyPatch, provider: LiteLLMProvider
) -> None:
    async def fail(**kwargs: Any) -> Any:
        raise _err(llm_errors.AuthenticationError)

    monkeypatch.setattr(litellm_provider.litellm, "acompletion", fail)
    with pytest.raises(ProviderError) as info:
        await provider.complete(request())
    assert info.value.code == "auth" and "gsk-test" not in str(info.value)


async def test_stream_yields_deltas_then_completion(
    monkeypatch: pytest.MonkeyPatch, provider: LiteLLMProvider
) -> None:
    def delta(text: str | None, finish: str | None = None) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)],
            usage=None,
        )

    async def chunks() -> AsyncIterator[Any]:
        for item in (delta("Life "), delta("[Art. 21]."), delta(None, "stop")):
            yield item
        yield SimpleNamespace(
            choices=[], usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4)
        )

    async def fake_acompletion(**kwargs: Any) -> Any:
        assert kwargs["stream"] and kwargs["stream_options"] == {"include_usage": True}
        return chunks()

    monkeypatch.setattr(litellm_provider.litellm, "acompletion", fake_acompletion)
    items = [item async for item in provider.stream(request())]
    assert items[:2] == ["Life ", "[Art. 21]."]
    assert items[2] == Completion("Life [Art. 21].", 9, 4, "stop")
