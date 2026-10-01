"""API contract snapshot (plan Phase 5 exit criteria; .claude/rules/api.md)."""

import json
from pathlib import Path

from samvidhan.api.openapi import openapi_document
from samvidhan.core.config import Settings

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"


def test_openapi_matches_the_committed_snapshot(settings: Settings) -> None:
    current = openapi_document(settings)
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current == committed, "API contract changed: run `make openapi` and commit the diff"


def test_v1_routes_are_all_there(settings: Settings) -> None:
    paths = openapi_document(settings)["paths"]
    assert {
        "/v1/sessions",
        "/v1/sessions/{session_id}/messages",
        "/v1/sessions/{session_id}",
        "/v1/chat",
        "/v1/messages/{message_id}/feedback",
        "/v1/articles/{article_no}",
        "/v1/meta",
    } <= set(paths)
