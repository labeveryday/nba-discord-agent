"""
Strands hooks for the NBA Discord agent.

Hooks intercept tool calls and results to fix known issues deterministically,
without relying on prompt engineering or model compliance.

Hooks:
    normalize_date_param    — converts any date format to YYYYMMDD
    prevent_duplicate_calls — cancels repeated identical tool calls in one turn
    truncate_long_results   — caps tool output to prevent context window bloat
    clean_tool_results      — strips IDs, logos, and URLs from output
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from strands.hooks.events import BeforeInvocationEvent, BeforeToolCallEvent, AfterToolCallEvent
from strands.plugins import Plugin, hook

log = logging.getLogger("hooks")

ET = ZoneInfo("America/New_York")

# Tools that accept a date parameter (YYYYMMDD format) — v0.3.0 names
DATE_TOOLS = {
    "nba_get_scoreboard": "date",
    "nba_find_game": "date",
}

# Max characters for a single tool result before truncation
MAX_RESULT_CHARS = 3000


def _normalize_date(value: str) -> str | None:
    """Try to parse a date string and return YYYYMMDD format.

    Handles: YYYYMMDD, YYYY-MM-DD, MM/DD/YYYY, Month D YYYY, etc.
    Returns None if parsing fails (let the tool handle it).
    """
    value = value.strip()
    if not value:
        return None

    if re.match(r'^\d{8}$', value):
        return value

    for fmt in (
        "%Y-%m-%d",       # 2026-04-03
        "%m/%d/%Y",       # 04/03/2026
        "%m-%d-%Y",       # 04-03-2026
        "%B %d, %Y",      # April 3, 2026
        "%B %d %Y",       # April 3 2026
        "%b %d, %Y",      # Apr 3, 2026
        "%b %d %Y",       # Apr 3 2026
        "%d %B %Y",       # 3 April 2026
        "%d %b %Y",       # 3 Apr 2026
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y%m%d")
        except ValueError:
            continue

    return None


# Regex patterns to strip from tool results — ordered for correct removal
# "| Team ID: 1610612765 | Logo: https://cdn.nba.com/..." (full pipe-delimited segment)
_PIPE_ID_LOGO_RE = re.compile(r'\s*\|\s*(?:Team|Player|Game)\s*ID:\s*\d+\s*\|\s*Logo:\s*\S+', re.IGNORECASE)
# Standalone "| Team ID: xxx" or "| Logo: url" segments
_PIPE_ID_RE = re.compile(r'\s*\|\s*(?:Team|Player|Game)\s*ID:\s*\d+', re.IGNORECASE)
_PIPE_LOGO_RE = re.compile(r'\s*\|\s*Logo:\s*\S+', re.IGNORECASE)
# NBA CDN URLs anywhere
_LOGO_URL_RE = re.compile(r'https?://cdn\.nba\.com/\S+', re.IGNORECASE)
# Markdown images
_MD_IMAGE_RE = re.compile(r'!\[.*?\]\(.*?\)')
# "Logo: View" or "Logo: <url>" as standalone text
_LOGO_REF_RE = re.compile(r'Logo:\s*\S+.*', re.IGNORECASE)
# "Team ID: 1610612766" not in pipe context
_LABELED_ID_RE = re.compile(r'(?:Team|Player|Game)\s*(?:ID|Id|id)\s*[:=]\s*\d{7,}', re.IGNORECASE)


class NBAToolHooks(Plugin):
    """Hooks that fix NBA tool calls and clean up results.

    BeforeToolCall:
        - Normalizes date parameters to YYYYMMDD
        - Cancels duplicate tool calls within the same turn

    AfterToolCall:
        - Truncates long results to prevent context bloat
        - Strips IDs, logos, and URLs
    """

    name = "nba-tool-hooks"

    def __init__(self) -> None:
        super().__init__()
        self._seen_calls: dict[tuple, bool] = {}

    def _call_key(self, tool_use: dict) -> tuple:
        """Create a hashable key from a tool call for dedup."""
        name = tool_use.get("name", "")
        inp = tool_use.get("input", {})
        if isinstance(inp, dict):
            return (name, tuple(sorted(inp.items())))
        return (name, str(inp))

    # --- Invocation hooks ---

    @hook
    def reset_dedup_tracker(self, event: BeforeInvocationEvent) -> None:
        """Clear duplicate call tracker at the start of each agent turn."""
        self._seen_calls.clear()

    # --- BeforeToolCall hooks ---

    @hook
    def normalize_date_param(self, event: BeforeToolCallEvent) -> None:
        """Normalize date parameters to YYYYMMDD format."""
        tool_name = event.tool_use["name"]
        date_param = DATE_TOOLS.get(tool_name)
        if not date_param:
            return

        tool_input = event.tool_use["input"]
        if not isinstance(tool_input, dict):
            return

        raw_date = tool_input.get(date_param)
        if not raw_date or not isinstance(raw_date, str):
            return

        if re.match(r'^\d{8}$', raw_date.strip()):
            return

        normalized = _normalize_date(raw_date)
        if normalized:
            tool_input[date_param] = normalized
            log.debug("Normalized date '%s' -> '%s' in %s", raw_date, normalized, tool_name)

    @hook
    def prevent_duplicate_calls(self, event: BeforeToolCallEvent) -> None:
        """Cancel duplicate tool calls with identical params in the same turn."""
        key = self._call_key(event.tool_use)

        if key in self._seen_calls:
            tool_name = event.tool_use["name"]
            log.info("Blocked duplicate call to %s", tool_name)
            event.cancel_tool = (
                f"You already called {tool_name} with the same parameters this turn. "
                f"Use the result you already received."
            )
            return

        self._seen_calls[key] = True

    # --- AfterToolCall hooks ---

    @hook
    def truncate_long_results(self, event: AfterToolCallEvent) -> None:
        """Cap tool results to prevent context window bloat."""
        if not event.result or event.exception:
            return

        content = event.result.get("content")
        if not content or not isinstance(content, list):
            return

        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue

            if len(text) > MAX_RESULT_CHARS:
                tool_name = event.tool_use["name"]
                truncated = text[:MAX_RESULT_CHARS]
                last_newline = truncated.rfind("\n")
                if last_newline > MAX_RESULT_CHARS // 2:
                    truncated = truncated[:last_newline]
                item["text"] = truncated + "\n\n(result truncated, ask for specific details if needed)"
                log.debug("Truncated %s result from %d to %d chars", tool_name, len(text), len(truncated))

    @hook
    def clean_tool_results(self, event: AfterToolCallEvent) -> None:
        """Strip IDs, logos, and URLs from tool results before the model sees them."""
        if not event.result or event.exception:
            return

        content = event.result.get("content")
        if not content or not isinstance(content, list):
            return

        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue

            cleaned = text
            cleaned = _PIPE_ID_LOGO_RE.sub("", cleaned)
            cleaned = _PIPE_ID_RE.sub("", cleaned)
            cleaned = _PIPE_LOGO_RE.sub("", cleaned)
            cleaned = _LOGO_URL_RE.sub("", cleaned)
            cleaned = _MD_IMAGE_RE.sub("", cleaned)
            cleaned = _LOGO_REF_RE.sub("", cleaned)
            cleaned = _LABELED_ID_RE.sub("", cleaned)
            cleaned = re.sub(r'\s*\|\s*$', '', cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

            if cleaned != text:
                item["text"] = cleaned
