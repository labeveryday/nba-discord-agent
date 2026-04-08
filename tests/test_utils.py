"""Tests for src/utils.py — pure helpers, no external deps."""

import pytest

from utils import (
    ConversationCache,
    chunk_for_discord,
    is_max_tokens_exception,
    truthy,
)


class TestTruthy:
    @pytest.mark.parametrize("v", ["1", "true", "TRUE", "Yes", "y", "on", " true "])
    def test_truthy_values(self, v):
        assert truthy(v) is True

    @pytest.mark.parametrize("v", ["0", "false", "no", "off", "", None, "maybe", "  "])
    def test_falsy_values(self, v):
        assert truthy(v) is False


class TestChunkForDiscord:
    def test_empty_returns_placeholder(self):
        assert chunk_for_discord("") == ["(no response)"]
        assert chunk_for_discord("   ") == ["(no response)"]
        assert chunk_for_discord(None) == ["(no response)"]

    def test_short_text_single_chunk(self):
        assert chunk_for_discord("hello") == ["hello"]

    def test_strips_outer_whitespace(self):
        assert chunk_for_discord("  hello  ") == ["hello"]

    def test_long_text_splits_at_newline(self):
        text = "a" * 1000 + "\n" + "b" * 1000 + "\n" + "c" * 500
        chunks = chunk_for_discord(text, limit=1500)
        assert len(chunks) >= 2
        # Each chunk fits the limit
        assert all(len(c) <= 1500 for c in chunks)
        # Reassembling preserves all original characters
        joined = "".join(chunks).replace("\n", "")
        assert joined.count("a") == 1000
        assert joined.count("b") == 1000
        assert joined.count("c") == 500

    def test_long_text_no_newline_hard_splits(self):
        text = "x" * 5000
        chunks = chunk_for_discord(text, limit=1900)
        assert len(chunks) == 3
        assert sum(len(c) for c in chunks) == 5000


class TestConversationCache:
    def test_get_or_create_caches(self):
        cache = ConversationCache(maxsize=3)
        calls = []
        v1 = cache.get_or_create("a", lambda: calls.append("a") or "agent-a")
        v2 = cache.get_or_create("a", lambda: calls.append("a2") or "agent-a2")
        assert v1 == v2 == "agent-a"
        assert calls == ["a"]  # factory only fired once

    def test_lru_eviction(self):
        cache = ConversationCache(maxsize=2)
        cache.get_or_create("a", lambda: "A")
        cache.get_or_create("b", lambda: "B")
        cache.get_or_create("c", lambda: "C")  # evicts "a"
        assert "a" not in cache
        assert "b" in cache
        assert "c" in cache

    def test_access_refreshes_lru_order(self):
        cache = ConversationCache(maxsize=2)
        cache.get_or_create("a", lambda: "A")
        cache.get_or_create("b", lambda: "B")
        # Touch "a" so "b" becomes the oldest
        cache.get_or_create("a", lambda: "A2")
        cache.get_or_create("c", lambda: "C")  # should evict "b", not "a"
        assert "a" in cache
        assert "b" not in cache
        assert "c" in cache


class TestIsMaxTokensException:
    def test_matches_by_class_name(self):
        class MaxTokensReachedException(Exception):
            pass

        assert is_max_tokens_exception(MaxTokensReachedException("oops")) is True

    def test_unrelated_exception(self):
        assert is_max_tokens_exception(ValueError("nope")) is False
