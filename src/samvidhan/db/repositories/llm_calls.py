"""`llm_calls` repository (HLD §10, observability §1.5–2): inserts and today's usage per model."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.db.models import LlmCall
from samvidhan.llm.types import CallRecord


class LlmCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, record: CallRecord) -> None:
        """Add one row; the caller commits."""
        self._session.add(
            LlmCall(
                request_id=record.request_id,
                session_id=record.session_id,
                purpose=record.purpose,
                provider=record.provider,
                model=record.model,
                prompt_version=record.prompt_version,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                latency_ms=record.latency_ms,
                ttft_ms=record.ttft_ms,
                cost_usd=record.cost_usd,
                status=record.status,
                error_code=record.error_code,
            )
        )

    async def count_since(self, model: str, since: datetime) -> int:
        """Rows (every status) for `model` created at or after `since`."""
        result = await self._session.execute(
            select(func.count())
            .select_from(LlmCall)
            .where(LlmCall.model == model, LlmCall.created_at >= since)
        )
        return int(result.scalar_one())
