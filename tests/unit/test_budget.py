"""BudgetGuard: caps, warn ratio, cache, fail open (spec §3.4)."""

import pytest
import structlog.testing

from samvidhan.core.config import Settings
from samvidhan.llm.budget import BudgetGuard


class Counter:
    def __init__(self, used: int) -> None:
        self.used = used
        self.queries = 0

    async def __call__(self, model: str) -> int:
        self.queries += 1
        return self.used


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(("used", "allowed"), [(0, True), (79, True), (80, False), (500, False)])
async def test_warn_ratio_boundary(used: int, allowed: bool) -> None:
    guard = BudgetGuard(Counter(used), {"m": 100}, warn_ratio=0.8)
    assert await guard.allows("m") is allowed


async def test_uncapped_model_never_queries() -> None:
    counter = Counter(10**6)
    guard = BudgetGuard(counter, {"other": 10}, warn_ratio=0.8)
    assert await guard.allows("m")
    assert counter.queries == 0


async def test_counts_are_cached_and_local_calls_are_added() -> None:
    counter, clock = Counter(78), Clock()
    guard = BudgetGuard(counter, {"m": 100}, warn_ratio=0.8, cache_s=30, clock=clock)
    assert await guard.allows("m")
    guard.note_call("m")
    guard.note_call("m")  # 78 + 2 = 80 → at the warn line
    assert not await guard.allows("m")
    assert counter.queries == 1
    clock.now = 31  # cache expired: re-read from the counter
    counter.used = 10
    assert await guard.allows("m")
    assert counter.queries == 2


async def test_counting_error_fails_open() -> None:
    async def broken(model: str) -> int:
        raise RuntimeError("db down")

    guard = BudgetGuard(broken, {"m": 1}, warn_ratio=0.8)
    with structlog.testing.capture_logs() as logs:
        assert await guard.allows("m")
    assert logs[0]["event"] == "budget_check_failed"


def test_daily_caps_from_settings(settings: Settings) -> None:
    s = settings.model_copy(
        update={
            "router_model": "groq/small",
            "answer_model": "groq/big",
            "router_fallback_model": "groq/big",  # same model in two roles → the lower cap
            "daily_cap_router_model": 14000,
            "daily_cap_answer_model": 1000,
            "daily_cap_router_fallback_model": 500,
            "answer_fallback_model": "gemini/flash",
            "daily_cap_answer_fallback_model": 0,  # 0 = no cap
        }
    )
    assert s.daily_caps() == {"groq/small": 14000, "groq/big": 500}
