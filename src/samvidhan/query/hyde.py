"""HyDE: a hypothetical Constitution-style passage for the dense leg (spec: llm-router-generation
§3.6, HLD §8.2). Best effort: any failure returns None and retrieval uses the standalone query."""

from samvidhan.core.config import Settings
from samvidhan.core.errors import SamvidhanError
from samvidhan.core.logging import get_logger
from samvidhan.llm.client import LLMClient
from samvidhan.llm.prompts import PromptTemplate
from samvidhan.llm.types import LLMRequest

log = get_logger(__name__)


async def hyde_passage(
    llm: LLMClient, settings: Settings, prompt: PromptTemplate, query: str
) -> str | None:
    request = LLMRequest(
        purpose="hyde",
        models=[settings.router_model, settings.router_fallback_model],
        messages=prompt.render(query=query),
        max_tokens=settings.hyde_max_tokens,
        temperature=settings.router_temperature,
        timeout_s=settings.llm_timeout_s,
        prompt_version=prompt.version,
    )
    try:
        result = await llm.complete(request)
    except SamvidhanError as exc:  # LLM unavailable or over budget: search without HyDE
        log.warning("hyde_failed", error=exc.code)
        return None
    passage = result.text.strip()
    if not passage:
        log.warning("hyde_failed", error="empty")
        return None
    return passage
