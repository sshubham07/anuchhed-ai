"""Versioned prompt files (standards §7): `prompts/<name>.vN.md`.

A file has a free-form header, then `<!-- system -->` and `<!-- user -->` sections. Placeholders are
`{{name}}`; rendering fails on a missing value so a template change can't silently drop context.
Values are inserted verbatim (no recursive expansion), so user text can't inject placeholders.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from samvidhan.llm.types import Message

_SECTION = re.compile(r"^<!--\s*(system|user)\s*-->\s*$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    version: str
    system: str
    user: str

    @property
    def placeholders(self) -> frozenset[str]:
        return frozenset(_PLACEHOLDER.findall(self.system + self.user))

    def render(self, **values: str) -> list[Message]:
        missing = self.placeholders - values.keys()
        if missing:
            raise KeyError(f"prompt {self.version} needs {sorted(missing)}")
        return [
            {"role": "system", "content": _fill(self.system, values)},
            {"role": "user", "content": _fill(self.user, values)},
        ]


def _fill(template: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template).strip()


def parse_prompt(version: str, source: str) -> PromptTemplate:
    parts = _SECTION.split(source)
    # ['header', 'system', '...', 'user', '...']
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    if set(sections) != {"system", "user"}:
        raise ValueError(f"prompt {version} needs exactly one <!-- system --> and <!-- user -->")
    return PromptTemplate(version, sections["system"].strip(), sections["user"].strip())


@lru_cache(maxsize=32)
def load_prompt(prompts_dir: Path, version: str) -> PromptTemplate:
    """`load_prompt(Path("prompts"), "router.v1")` reads `prompts/router.v1.md` (cached)."""
    path = prompts_dir / f"{version}.md"
    return parse_prompt(version, path.read_text(encoding="utf-8"))
