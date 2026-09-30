"""Plain data passed between ingestion steps (spec: ingestion §3.3). No I/O here."""

import re
from dataclasses import dataclass

# Extract writes each superscript footnote number into the text as a token, e.g. "{{fn:2}}[21A. …";
# the footnotes step resolves and strips it. Braces never occur in the Constitution text.
FOOTNOTE_TOKEN_RE = re.compile(r"\{\{fn:(\d+)\}\}")


@dataclass(frozen=True, slots=True)
class Line:
    """One visual row of a PDF page, rebuilt from spans that share the same vertical band.

    - `text`: the row's fragments joined with single spaces.
    - `parts`: the fragments that were separated by a wide horizontal gap (table cells).
    - `bold_prefix`: the leading run of bold text, whitespace-collapsed, ignoring footnote tokens.
      An Article heading is a bold prefix plus a non-bold body: `21. Title.` + `—No person…`.
    - `size`: the dominant font size of the row, ignoring footnote numbers.
    """

    page: int  # 1-based
    text: str
    size: float
    bold_prefix: str
    y: float  # top of the row, PDF points
    x0: float  # left edge of the row, PDF points (indentation)
    parts: tuple[str, ...]

    @property
    def is_bold(self) -> bool:
        """The whole row is bold (ignoring footnote tokens), e.g. `PART III` or a group heading."""
        plain = " ".join(strip_footnote_tokens(self.text).split())
        return bool(self.bold_prefix) and self.bold_prefix == plain


def footnote_token(n: int) -> str:
    return f"{{{{fn:{n}}}}}"


def strip_footnote_tokens(text: str) -> str:
    """Text without `{{fn:N}}` tokens, trimmed."""
    return FOOTNOTE_TOKEN_RE.sub("", text).strip()
