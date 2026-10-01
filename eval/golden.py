"""Golden-set schema and loader (spec: evaluation.md §2)."""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

GOLDEN_DIR = Path(__file__).parent / "golden"
SINGLE_TURN = GOLDEN_DIR / "single_turn.jsonl"
VERSION_FILE = GOLDEN_DIR / "VERSION"

Split = Literal["dev", "test"]
CANONICAL_REF = re.compile(r"^(\d{1,3}[A-Z]{0,3}|SCH-(?:[1-9]|1[0-2])|PREAMBLE|APP-(?:I|II|III))$")


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    split: Split
    category: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    question: str
    expected_type: str
    expected_refs: list[str] = []
    acceptable_refs: list[str] = []
    question_refs: list[str] = []
    reference_answer: str = ""
    must_refuse: bool = False
    expected_answer_style: Literal["brief", "detailed", "exam"] = "brief"
    expected_word_limit: int | None = None
    source: Literal["manual", "synthetic", "production"] = "manual"
    notes: str = ""

    @field_validator("expected_refs", "acceptable_refs", "question_refs")
    @classmethod
    def _canonical(cls, refs: list[str]) -> list[str]:
        bad = [ref for ref in refs if not CANONICAL_REF.match(ref)]
        if bad:
            raise ValueError(f"non-canonical refs {bad} (use 21A, SCH-7, PREAMBLE, APP-I)")
        return refs

    @property
    def retrievable(self) -> bool:
        """Scored by the retrieval suite: has expected refs and is not a refusal case."""
        return bool(self.expected_refs) and not self.must_refuse


def load_cases(path: Path = SINGLE_TURN, split: Split | None = None) -> list[GoldenCase]:
    cases = [
        GoldenCase.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case ids in {path}")
    return [case for case in cases if split is None or case.split == split]


def golden_version() -> str:
    return (
        VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "unversioned"
    )
