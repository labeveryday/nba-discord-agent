"""Tests for src/alerts.py — webhook payload shape and silent-when-unset behavior."""

import json
from unittest.mock import MagicMock, patch

import pytest

import alerts


@pytest.fixture(autouse=True)
def reset_ollama_state():
    """Reset the module-level Ollama state between tests."""
    alerts._last_ollama_state = None
    yield
    alerts._last_ollama_state = None


class TestSendAlert:
    def test_silent_when_webhook_unset(self, monkeypatch):
        monkeypatch.delenv("ALERTS_WEBHOOK_URL", raising=False)
        with patch.object(alerts, "urlopen") as fake:
            alerts.send_alert("hi", "msg")
        fake.assert_not_called()

    def test_posts_embed_payload(self, monkeypatch):
        monkeypatch.setenv("ALERTS_WEBHOOK_URL", "https://example.invalid/webhook")
        with patch.object(alerts, "urlopen") as fake:
            fake.return_value.__enter__.return_value = MagicMock()
            alerts.send_alert("Title", "Body", level="warning")
            assert fake.call_count == 1
            req = fake.call_args[0][0]
            payload = json.loads(req.data)
            assert payload["username"] == "NBA Agent Alerts"
            assert payload["embeds"][0]["title"] == "Title"
            assert payload["embeds"][0]["description"] == "Body"
            assert payload["embeds"][0]["color"] == 15105570  # warning/orange

    def test_swallows_network_errors(self, monkeypatch):
        from urllib.error import URLError

        monkeypatch.setenv("ALERTS_WEBHOOK_URL", "https://example.invalid/webhook")
        with patch.object(alerts, "urlopen", side_effect=URLError("nope")):
            # Must not raise — alerts should never break the bot
            alerts.send_alert("t", "m")


class TestOllamaCheckTransitions:
    def test_first_call_records_no_alert(self, monkeypatch):
        monkeypatch.setenv("ALERTS_WEBHOOK_URL", "https://example.invalid/webhook")
        with patch.object(alerts, "send_alert") as fake:
            alerts.alert_ollama_check(True)
        fake.assert_not_called()
        assert alerts._last_ollama_state is True

    def test_alerts_on_down_transition(self, monkeypatch):
        monkeypatch.setenv("ALERTS_WEBHOOK_URL", "https://example.invalid/webhook")
        alerts._last_ollama_state = True  # was up
        with patch.object(alerts, "send_alert") as fake:
            alerts.alert_ollama_check(False)
        fake.assert_called_once()
        assert "Unreachable" in fake.call_args[0][0]

    def test_alerts_on_recovery_transition(self, monkeypatch):
        monkeypatch.setenv("ALERTS_WEBHOOK_URL", "https://example.invalid/webhook")
        alerts._last_ollama_state = False
        with patch.object(alerts, "send_alert") as fake:
            alerts.alert_ollama_check(True)
        fake.assert_called_once()
        assert "Recovered" in fake.call_args[0][0]

    def test_no_alert_when_state_unchanged(self):
        alerts._last_ollama_state = True
        with patch.object(alerts, "send_alert") as fake:
            alerts.alert_ollama_check(True)
        fake.assert_not_called()


class TestActionAlerts:
    def test_heartbeat_actions_silent_when_no_actions(self):
        with patch.object(alerts, "send_alert") as fake:
            alerts.alert_heartbeat_actions([], 0.0)
        fake.assert_not_called()

    def test_heartbeat_actions_sends_when_actions_present(self):
        with patch.object(alerts, "send_alert") as fake:
            alerts.alert_heartbeat_actions(["morning_recap", "rise_and_grind"], 12.5)
        fake.assert_called_once()
        title, body = fake.call_args[0][:2]
        assert "Heartbeat" in title
        assert "morning_recap" in body
        assert "rise_and_grind" in body
