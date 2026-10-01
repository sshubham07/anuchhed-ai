"""Templated replies — no LLM call (spec: llm-router-generation §3.9, HLD §8.2)."""

import re

DEFAULT_CLARIFICATION = (
    "Could you tell me a bit more about what you're looking for? For example, a topic such as "
    "Fundamental Rights, Parliament, elections or emergency provisions — or a specific Article."
)

OUT_OF_SCOPE = (
    "I can only answer questions about the text of the Constitution of India. I don't cover other "
    "laws (such as the IPC/BNS or CrPC/BNSS), court judgments, current events or other countries. "
    'You could ask, for example, "What does Article 21 say?", "Who appoints the Chief Election '
    'Commissioner?" or "How can the Constitution be amended?"'
)

_ABOUT = (
    "I answer questions about the Constitution of India, citing the Articles I rely on. "
    'Try asking "What does Article 14 say?" or "Can the police arrest me without telling me why?"'
)
_THANKS = re.compile(r"\b(thanks?|thank\s*you|thx|ty|great|helpful)\b", re.IGNORECASE)

NOT_FOUND = (
    "I couldn't find anything in the text of the Constitution that answers this. The Constitution "
    "may not cover it, or it may be phrased differently — try naming an Article or using "
    "different words."
)


def clarification(question: str | None) -> str:
    return (question or "").strip() or DEFAULT_CLARIFICATION


def chitchat(message: str) -> str:
    if _THANKS.search(message):
        return "You're welcome! " + _ABOUT
    return "Hello! " + _ABOUT
