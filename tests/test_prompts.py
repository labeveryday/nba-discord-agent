"""Tests for src/config/prompts.py — date/season logic in the system prompt."""

from datetime import datetime
from unittest.mock import patch

from config.prompts import build_system_prompt


def _freeze(dt: datetime):
    """Patch datetime.now() inside config.prompts to return a fixed value."""

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt if tz is None else dt.replace(tzinfo=tz)

    return patch("config.prompts.datetime", FakeDT)


class TestBuildSystemPrompt:
    def test_october_starts_new_season(self):
        with _freeze(datetime(2026, 10, 22)):
            prompt = build_system_prompt()
        assert "2026-27" in prompt
        assert "Last season" in prompt
        assert "2025-26" in prompt

    def test_january_uses_previous_calendar_year(self):
        with _freeze(datetime(2026, 1, 15)):
            prompt = build_system_prompt()
        # Jan 2026 is still the 2025-26 season
        assert "2025-26" in prompt
        assert "2024-25" in prompt  # last season

    def test_june_finals_still_current_season(self):
        with _freeze(datetime(2026, 6, 5)):
            prompt = build_system_prompt()
        assert "2025-26" in prompt

    def test_contains_formatting_rules(self):
        prompt = build_system_prompt()
        assert "NEVER show internal IDs" in prompt
        assert "team names only" in prompt
        assert "Discord" in prompt

    def test_mentions_today_in_human_format(self):
        with _freeze(datetime(2026, 4, 8)):
            prompt = build_system_prompt()
        assert "April 8, 2026" in prompt
