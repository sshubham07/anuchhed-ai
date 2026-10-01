"""Real provider calls (marker `llm`, excluded from CI). Skips without keys in `.env`.

uv run pytest -m llm
"""

import pytest
from pydantic import SecretStr

from samvidhan.core.config import Settings, get_settings
from samvidhan.llm.client import LLMClient, is_configured
from samvidhan.llm.litellm_provider import LiteLLMProvider
from samvidhan.llm.prompts import load_prompt
from samvidhan.llm.recorder import MemoryCallRecorder
from samvidhan.llm.types import LLMRequest
from samvidhan.memory.types import SessionMemory
from samvidhan.query.router import route

pytestmark = pytest.mark.llm


@pytest.fixture
def live() -> Settings:
    settings = get_settings()  # the developer's .env, keys included
    if not settings.groq_api_key.get_secret_value():
        pytest.skip("GROQ_API_KEY not set")
    return settings


def client(settings: Settings) -> tuple[LLMClient, MemoryCallRecorder]:
    recorder = MemoryCallRecorder()
    return LLMClient(LiteLLMProvider(settings), recorder, max_retries=0), recorder


async def test_router_live(live: Settings) -> None:
    llm, recorder = client(live)
    prompt = load_prompt(live.prompts_dir, live.router_prompt_version)
    decision = await route(llm, live, prompt, "What does Art. 21-A say?", SessionMemory())
    assert not decision.fallback
    assert decision.type == "article_lookup" and "21A" in decision.article_refs
    assert recorder.records[0].status == "ok" and recorder.records[0].input_tokens


async def test_bad_primary_key_falls_back_to_gemini(live: Settings) -> None:
    if not (live.gemini_api_key.get_secret_value() and is_configured(live.router_fallback_model)):
        pytest.skip("GEMINI_API_KEY or a real ROUTER_FALLBACK_MODEL not set")
    broken = live.model_copy(update={"groq_api_key": SecretStr("gsk_invalid")})
    llm, recorder = client(broken)
    result = await llm.complete(
        LLMRequest(
            purpose="router",
            models=[broken.router_model, broken.router_fallback_model],
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=5,
            temperature=0,
            timeout_s=broken.llm_timeout_s,
        )
    )
    assert result.status == "fallback" and result.provider == "gemini" and result.text
    assert [(r.status, r.error_code) for r in recorder.records] == [
        ("error", "auth"),
        ("fallback", None),
    ]
