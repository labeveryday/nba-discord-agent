"""Pure utility helpers — no Discord/Strands/MCP imports.

Kept import-light so they can be unit-tested in CI without installing the full
agent runtime.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

DISCORD_CHUNK_LIMIT = 1900
DEFAULT_MAX_CONVERSATIONS = 100


def truthy(value: Optional[str]) -> bool:
    """Parse the common boolean-ish strings used in env vars."""
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def chunk_for_discord(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> list[str]:
    """Split a long string into Discord-sized chunks, preferring newline breaks."""
    text = (text or "").strip()
    if not text:
        return ["(no response)"]

    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks


class ConversationCache(OrderedDict):
    """LRU-evicting dict that caps the number of active conversations."""

    def __init__(self, maxsize: int = DEFAULT_MAX_CONVERSATIONS):
        super().__init__()
        self._maxsize = maxsize

    def get_or_create(self, key: str, factory):
        if key in self:
            self.move_to_end(key)
            return self[key]
        if len(self) >= self._maxsize:
            self.popitem(last=False)
        value = factory()
        self[key] = value
        return value


def is_max_tokens_exception(e: Exception) -> bool:
    """Detect Strands' MaxTokensReachedException without a hard import dependency."""
    return type(e).__name__ == "MaxTokensReachedException"
