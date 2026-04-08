"""
Alerts module — sends monitoring events to a Discord webhook.

Uses ALERTS_WEBHOOK_URL so it works with private channels and shows
a distinct identity from the bot's NBA posts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from urllib.request import urlopen, Request
from urllib.error import URLError

log = logging.getLogger("alerts")

_last_ollama_state: bool | None = None


def _webhook_url() -> str | None:
    return os.environ.get("ALERTS_WEBHOOK_URL")


def send_alert(title: str, message: str, level: str = "info") -> None:
    """Send an alert embed to the Discord webhook."""
    url = _webhook_url()
    if not url:
        return

    colors = {
        "info": 3447003,      # blue
        "success": 3066993,   # green
        "warning": 15105570,  # orange
        "error": 15158332,    # red
    }

    payload = {
        "username": "NBA Agent Alerts",
        "embeds": [{
            "title": title,
            "description": message,
            "color": colors.get(level, 3447003),
            "footer": {"text": "NBA Discord Agent"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }]
    }

    try:
        data = json.dumps(payload).encode()
        req = Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "nba-discord-agent/1.0",
        })
        with urlopen(req, timeout=10):
            pass
    except (URLError, OSError) as e:
        log.error("Failed to send alert: %s", e)


def alert_startup(model: str, heartbeat_enabled: bool) -> None:
    hb = "active" if heartbeat_enabled else "disabled"
    send_alert("Agent Online", f"**Model:** {model}\n**Heartbeat:** {hb}", "success")


def alert_heartbeat_actions(actions: list[str], duration_s: float) -> None:
    if not actions:
        return
    action_list = ", ".join(actions)
    send_alert("Heartbeat Actions", f"**Actions:** {action_list}\n**Duration:** {duration_s:.0f}s", "info")


def alert_heartbeat_error(error: str) -> None:
    send_alert("Heartbeat Error", f"```{str(error)[:500]}```", "error")


def alert_agent_error(context: str, error: str) -> None:
    send_alert("Agent Error", f"**Context:** {context}\n```{str(error)[:500]}```", "error")


def alert_ollama_check(reachable: bool) -> None:
    """Alert on Ollama state changes only (not every check)."""
    global _last_ollama_state
    if _last_ollama_state is None:
        _last_ollama_state = reachable
        return
    if _last_ollama_state and not reachable:
        send_alert("Ollama Unreachable", "Heartbeat and interactive queries are down.", "error")
    elif not _last_ollama_state and reachable:
        send_alert("Ollama Recovered", "Connection restored.", "success")
    _last_ollama_state = reachable
