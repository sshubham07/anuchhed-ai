"""App wiring for API tests: real `assemble_services` over fakes, so no model loads and no
provider calls. The engine is lazy; unit tests point it at an unreachable DB."""

from typing import cast

from samvidhan.api.services import AppServices, ServicesFactory, assemble_services
from samvidhan.core.config import Settings
from samvidhan.db.engine import create_engine
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.recorder import CallRecorder, MemoryCallRecorder
from samvidhan.retrieval.service import RetrievalService
from tests.unit.graph_helpers import StubRetrieval


def fake_services(
    provider: FakeProvider | None = None,
    retrieval: object | None = None,
    recorder: CallRecorder | None = None,
) -> ServicesFactory:
    async def factory(settings: Settings) -> AppServices:
        llm = fake_llm(provider or FakeProvider(), recorder or MemoryCallRecorder())
        return await assemble_services(
            settings,
            create_engine(settings),
            llm,
            cast(RetrievalService, retrieval or StubRetrieval()),
        )

    return factory
