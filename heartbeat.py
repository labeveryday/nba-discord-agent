"""
Heartbeat system for proactive NBA Discord posts.

Runs as an asyncio background task alongside the Discord event loop.
Checks time-of-day schedules and posts recaps, previews, scores, and standings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError
from zoneinfo import ZoneInfo

import discord

log = logging.getLogger("heartbeat")

ET = ZoneInfo("America/New_York")  # Handles EDT/EST automatically

# Game hours window (ET) — when to check for live score updates
GAME_HOURS_START = 12  # noon ET (early weekend games)
GAME_HOURS_END = 2  # 2 AM ET (late West Coast games)

SCORE_CHECK_INTERVAL = 900  # 15 minutes between live score checks
AGENT_TIMEOUT = 120  # seconds — max time for a single agent call

NBA_SCOREBOARD_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"


# ---------------------------------------------------------------------------
# SQLite helpers — all use context managers to prevent connection leaks
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return os.environ.get("SQLITE_DB_PATH", "/app/data/agent.db")


@contextmanager
def _db():
    """Yield a SQLite connection that is always closed, even on error."""
    path = _db_path()
    conn = sqlite3.connect(path)
    try:
        yield conn
    finally:
        conn.close()


def _init_db() -> None:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS heartbeat_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                job_key TEXT NOT NULL UNIQUE,
                posted_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS game_threads (
                game_id TEXT PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS game_state (
                game_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hb_key ON heartbeat_log(job_key)")
        conn.commit()


def _already_posted(job_type: str, key: str) -> bool:
    job_key = f"{job_type}:{key}"
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM heartbeat_log WHERE job_key = ?", (job_key,)
        ).fetchone()
    return row is not None


def _mark_posted(job_type: str, key: str) -> None:
    job_key = f"{job_type}:{key}"
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO heartbeat_log (job_type, job_key, posted_at) VALUES (?, ?, ?)",
            (job_type, job_key, int(time.time())),
        )
        conn.commit()


def _get_game_state(game_id: str) -> Optional[str]:
    with _db() as conn:
        row = conn.execute(
            "SELECT status FROM game_state WHERE game_id = ?", (game_id,)
        ).fetchone()
    return row[0] if row else None


def _set_game_state(game_id: str, status: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO game_state (game_id, status, updated_at) VALUES (?, ?, ?)",
            (game_id, status, int(time.time())),
        )
        conn.commit()


def _save_thread(game_id: str, thread_id: int) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO game_threads (game_id, thread_id, created_at) VALUES (?, ?, ?)",
            (game_id, thread_id, int(time.time())),
        )
        conn.commit()


def _get_thread_id(game_id: str) -> Optional[int]:
    with _db() as conn:
        row = conn.execute(
            "SELECT thread_id FROM game_threads WHERE game_id = ?", (game_id,)
        ).fetchone()
    return row[0] if row else None


def _prune_stale_data(max_age_days: int = 7) -> None:
    """Remove game_state and game_threads entries older than max_age_days."""
    cutoff = int(time.time()) - (max_age_days * 86400)
    with _db() as conn:
        conn.execute("DELETE FROM game_state WHERE updated_at < ?", (cutoff,))
        conn.execute("DELETE FROM game_threads WHERE created_at < ?", (cutoff,))
        conn.commit()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now_et() -> datetime:
    return datetime.now(ET)


def _today_key() -> str:
    return _now_et().strftime("%Y-%m-%d")


def _yesterday_key() -> str:
    return (_now_et() - timedelta(days=1)).strftime("%Y-%m-%d")


def _week_key() -> str:
    d = _now_et()
    monday = d - timedelta(days=d.weekday())
    return monday.strftime("%Y-W%W")


def _is_game_hours() -> bool:
    hour = _now_et().hour
    if GAME_HOURS_START <= GAME_HOURS_END:
        return GAME_HOURS_START <= hour < GAME_HOURS_END
    # Wraps midnight (e.g., 12 to 2)
    return hour >= GAME_HOURS_START or hour < GAME_HOURS_END


# ---------------------------------------------------------------------------
# NBA API — direct calls, no LLM needed for data fetching
# ---------------------------------------------------------------------------

def _fetch_scoreboard() -> list[dict]:
    """Fetch today's scoreboard directly from the NBA Live API."""
    try:
        req = Request(NBA_SCOREBOARD_URL, headers={"User-Agent": "nba-discord-agent/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("scoreboard", {}).get("games", [])
    except (URLError, json.JSONDecodeError, KeyError, OSError) as e:
        log.error("Failed to fetch NBA scoreboard: %s", e)
        return []


def _parse_games(games: list[dict]) -> list[dict]:
    """Parse raw NBA API game objects into a clean format."""
    parsed = []
    for g in games:
        away = g.get("awayTeam", {})
        home = g.get("homeTeam", {})
        status_text = str(g.get("gameStatusText", "")).strip()
        parsed.append({
            "game_id": g.get("gameId", ""),
            "away_tri": away.get("teamTricode", "???"),
            "away_name": away.get("teamName", "Away"),
            "away_score": away.get("score", 0),
            "home_tri": home.get("teamTricode", "???"),
            "home_name": home.get("teamName", "Home"),
            "home_score": home.get("score", 0),
            "status_text": status_text,
            "is_final": "final" in status_text.lower(),
            "game_status": g.get("gameStatus", 0),  # 1=scheduled, 2=in progress, 3=final
            "game_time_utc": g.get("gameTimeUTC", ""),
            "game_et": g.get("gameEt", ""),
        })
    return parsed


def _get_final_games(parsed: list[dict]) -> list[dict]:
    return [g for g in parsed if g["is_final"]]


def _format_scoreboard_for_llm(games: list[dict], label: str = "Games") -> str:
    """Format parsed games into a text block the LLM can read. No IDs exposed."""
    if not games:
        return f"No {label.lower()} found."
    lines = [f"{label} ({len(games)} total):\n"]
    for g in games:
        away = f"{g['away_name']} ({g['away_tri']})"
        home = f"{g['home_name']} ({g['home_tri']})"
        score = f"{g['away_score']}-{g['home_score']}"
        lines.append(f"  {away} {score} @ {home} — {g['status_text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------

def _heartbeat_channel_id() -> Optional[int]:
    val = os.environ.get("HEARTBEAT_CHANNEL_ID")
    return int(val) if val else None


def _game_thread_channel_id() -> Optional[int]:
    val = os.environ.get("GAME_THREAD_CHANNEL_ID") or os.environ.get("HEARTBEAT_CHANNEL_ID")
    return int(val) if val else None


def chunk_for_discord(text: str, limit: int = 1900) -> list[str]:
    """Split text into Discord-safe chunks."""
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


async def _send_chunked(channel, text: str) -> None:
    """Send a message to Discord, splitting into chunks if over the limit."""
    for part in chunk_for_discord(text):
        await channel.send(part)


# ---------------------------------------------------------------------------
# Agent helper — run with timeout and yield to interactive users
# ---------------------------------------------------------------------------

async def run_agent_proactive(
    agent, prompt: str, semaphore: asyncio.Semaphore, timeout: int = AGENT_TIMEOUT
) -> Optional[str]:
    """Run the proactive agent, yielding if interactive users are active.

    Returns None if the semaphore is busy, the call times out, or an error occurs.
    """
    if semaphore.locked():
        log.info("Semaphore busy (user being served), skipping heartbeat job")
        return None
    try:
        async with semaphore:
            result = await asyncio.wait_for(
                asyncio.to_thread(agent, prompt),
                timeout=timeout,
            )
            return str(result).strip()
    except asyncio.TimeoutError:
        log.error("Proactive agent timed out after %ds", timeout)
        return None
    except Exception as e:
        log.error("Proactive agent error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Heartbeat jobs
# ---------------------------------------------------------------------------

async def post_morning_recap(
    agent, client: discord.Client, semaphore: asyncio.Semaphore
) -> None:
    """Post yesterday's game results using direct API + LLM for writing."""
    yesterday = _yesterday_key()
    if _already_posted("recap", yesterday):
        return

    channel_id = _heartbeat_channel_id()
    if not channel_id:
        return
    channel = client.get_channel(channel_id)
    if not channel:
        log.warning("Heartbeat channel %s not found", channel_id)
        return

    # Fetch yesterday's scoreboard directly — no LLM needed for data
    games = await asyncio.to_thread(_fetch_scoreboard)
    parsed = _parse_games(games)
    finals = _get_final_games(parsed)

    if not finals:
        log.info("Morning recap: no final games yesterday")
        _mark_posted("recap", yesterday)
        return

    # Build structured data for the LLM — it only needs to WRITE, not fetch
    game_summary = _format_scoreboard_for_llm(finals, "Last Night's Results")

    prompt = (
        f"Here are last night's NBA results ({yesterday}):\n\n"
        f"{game_summary}\n\n"
        f"Write a morning recap for Discord:\n"
        f"- Start with a greeting like 'Good morning! Here's what happened last night:'\n"
        f"- Each game on one line: **Winner score** - Loser score\n"
        f"- Pick a 'Player of the Night' based on the scores (highest-scoring team likely had the top performer)\n"
        f"- Keep it concise and Discord-friendly. No IDs, no image URLs.\n"
        f"- If you want more detail on a specific game's top performer, use get_box_score with that game's ID."
    )

    response = await run_agent_proactive(agent, prompt, semaphore)
    if not response:
        return

    await _send_chunked(channel, f"🏀 **Morning Recap — {yesterday}**\n\n{response}")
    _mark_posted("recap", yesterday)
    log.info("Posted morning recap for %s (%d games)", yesterday, len(finals))


async def post_gameday_preview(
    agent, client: discord.Client, semaphore: asyncio.Semaphore
) -> None:
    """Post today's game schedule using direct API + LLM for writing."""
    today = _today_key()
    if _already_posted("preview", today):
        return

    channel_id = _heartbeat_channel_id()
    if not channel_id:
        return
    channel = client.get_channel(channel_id)
    if not channel:
        return

    # Fetch today's scoreboard directly
    games = await asyncio.to_thread(_fetch_scoreboard)
    parsed = _parse_games(games)

    if not parsed:
        log.info("Game-day preview: no games today")
        _mark_posted("preview", today)
        return

    game_summary = _format_scoreboard_for_llm(parsed, "Today's Games")

    prompt = (
        f"Here are today's NBA games ({today}):\n\n"
        f"{game_summary}\n\n"
        f"Write a game-day preview for Discord:\n"
        f"- Each matchup: Away @ Home | tip time if shown | note anything interesting\n"
        f"- Flag any notable matchups (rivalry, playoff implications)\n"
        f"- Keep it concise. No IDs, no image URLs."
    )

    response = await run_agent_proactive(agent, prompt, semaphore)
    if not response:
        return

    await _send_chunked(channel, f"📋 **Today's Games — {today}**\n\n{response}")
    _mark_posted("preview", today)
    log.info("Posted game-day preview for %s (%d games)", today, len(parsed))


async def create_game_threads(
    client: discord.Client,
) -> None:
    """Create Discord threads for today's games using direct API. No LLM needed."""
    today = _today_key()
    if _already_posted("threads", today):
        return

    channel_id = _game_thread_channel_id()
    if not channel_id:
        return
    channel = client.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    # Fetch directly — no LLM needed for structured data
    games = await asyncio.to_thread(_fetch_scoreboard)
    parsed = _parse_games(games)

    if not parsed:
        _mark_posted("threads", today)
        return

    date_short = _now_et().strftime("%b %-d")
    created = 0

    for game in parsed:
        away = game["away_name"]
        home = game["home_name"]
        game_id = game["game_id"]

        # Extract time — gameEt is UTC (ends with Z), convert to ET
        game_et = game.get("game_et", "")
        time_str = ""
        if "T" in game_et:
            try:
                utc_str = game_et.replace("Z", "+00:00")
                utc_dt = datetime.fromisoformat(utc_str)
                et_dt = utc_dt.astimezone(ET)
                hour = et_dt.hour
                minute = et_dt.minute
                ampm = "AM" if hour < 12 else "PM"
                display_hour = hour if hour <= 12 else hour - 12
                if display_hour == 0:
                    display_hour = 12
                time_str = f"{display_hour}:{minute:02d} {ampm} ET"
            except (ValueError, IndexError):
                pass

        thread_name = f"{away} @ {home} | {date_short}"
        if time_str:
            thread_name += f" | {time_str}"

        try:
            thread = await channel.create_thread(
                name=thread_name[:100],
                type=discord.ChannelType.public_thread,
                auto_archive_duration=1440,  # 24 hours
            )
            if game_id:
                _save_thread(game_id, thread.id)
            await thread.send(f"🏀 **{away} @ {home}** — Game thread! Discuss the game here.")
            created += 1
            log.info("Created thread: %s (game_id=%s)", thread_name, game_id)
        except discord.HTTPException as e:
            log.error("Failed to create thread for %s @ %s: %s", away, home, e)

    _mark_posted("threads", today)
    log.info("Created %d game threads for %s", created, today)


async def check_postgame_highlights(
    agent, client: discord.Client, semaphore: asyncio.Semaphore
) -> None:
    """Check for newly-finished games and post highlights."""
    channel_id = _heartbeat_channel_id()
    if not channel_id:
        return

    # Fetch scoreboard directly — no LLM needed for this check
    games = await asyncio.to_thread(_fetch_scoreboard)
    if not games:
        log.info("Post-game check: no games on scoreboard")
        return

    parsed = _parse_games(games)
    finals = _get_final_games(parsed)
    if not finals:
        log.info("Post-game check: %d games on scoreboard, none final yet", len(parsed))
        return

    log.info("Found %d final game(s) out of %d total", len(finals), len(parsed))

    for game in finals:
        game_id = game["game_id"]
        if not game_id:
            continue

        # Only post if we haven't already
        prev_status = _get_game_state(game_id)
        if prev_status == "Final":
            continue

        away = game["away_name"]
        home = game["home_name"]
        away_score = game["away_score"]
        home_score = game["home_score"]

        log.info("Generating highlight for %s %s @ %s %s (game_id=%s)",
                 away, away_score, home, home_score, game_id)

        highlight_prompt = (
            f"Use get_box_score with game_id={game_id} to get the box score. "
            f"The final score was {away} {away_score} - {home} {home_score}. "
            f"Write a 3-sentence post-game highlight:\n"
            f"- Line 1: Final score with winner bolded\n"
            f"- Line 2: Top performer from each team (name + pts/reb/ast)\n"
            f"- Line 3: One notable stat or storyline\n"
            f"No IDs, no image URLs."
        )

        highlight = await run_agent_proactive(agent, prompt=highlight_prompt, semaphore=semaphore)
        if not highlight:
            log.warning("Highlight generation failed for game_id=%s", game_id)
            continue

        # Post to game thread if one exists, otherwise heartbeat channel
        target = None
        thread_id = _get_thread_id(game_id)
        if thread_id:
            target = client.get_channel(thread_id)
        if not target:
            target = client.get_channel(channel_id)

        if target:
            await _send_chunked(target, f"🚨 **Final Score**\n\n{highlight}")
            # Mark as Final AFTER successful send — so we retry on failure
            _set_game_state(game_id, "Final")
            log.info("Posted post-game highlight for %s @ %s (game_id=%s)", away, home, game_id)
        else:
            log.error("Could not find channel to post highlight (channel_id=%s, thread_id=%s)",
                       channel_id, thread_id)


async def post_weekly_standings(
    agent, client: discord.Client, semaphore: asyncio.Semaphore
) -> None:
    """Post weekly standings on Monday."""
    week = _week_key()
    if _already_posted("standings", week):
        return

    channel_id = _heartbeat_channel_id()
    if not channel_id:
        return
    channel = client.get_channel(channel_id)
    if not channel:
        return

    prompt = (
        "Use get_standings to get current NBA standings. "
        "Format them for Discord:\n"
        "- Split by Eastern and Western conference\n"
        "- Show: Rank | Team | W-L | Win% | GB | Streak\n"
        "- Use a clean table format that looks good in Discord\n"
        "- Flag teams on 5+ game win/loss streaks\n"
        "Keep it clean and readable. No IDs."
    )

    response = await run_agent_proactive(agent, prompt, semaphore)
    if not response:
        return

    await _send_chunked(channel, f"📊 **Weekly Standings — {week}**\n\n{response}")
    _mark_posted("standings", week)
    log.info("Posted weekly standings for %s", week)


# ---------------------------------------------------------------------------
# Main heartbeat loop
# ---------------------------------------------------------------------------

async def heartbeat_loop(
    make_agent,
    client: discord.Client,
    semaphore: asyncio.Semaphore,
) -> None:
    """Main heartbeat loop — runs as asyncio background task."""
    _init_db()
    await client.wait_until_ready()

    # Let the bot fully connect before starting heartbeat
    await asyncio.sleep(10)

    proactive_agent = make_agent()
    last_score_check = 0.0
    last_prune = time.monotonic()

    log.info("Heartbeat loop started")

    while not client.is_closed():
        try:
            now = _now_et()

            # Morning recap at 9 AM ET
            if now.hour == 9:
                await post_morning_recap(proactive_agent, client, semaphore)

            # Game-day preview + threads at 11 AM ET
            if now.hour == 11:
                await post_gameday_preview(proactive_agent, client, semaphore)
                await create_game_threads(client)

            # Post-game highlights during game hours (check every 15 min)
            if _is_game_hours():
                elapsed = time.monotonic() - last_score_check
                if elapsed >= SCORE_CHECK_INTERVAL:
                    log.info("Game hours active (hour=%d), running score check (%.0fs since last)",
                             now.hour, elapsed)
                    await check_postgame_highlights(proactive_agent, client, semaphore)
                    last_score_check = time.monotonic()

            # Weekly standings on Monday at 10 AM ET
            if now.weekday() == 0 and now.hour == 10:
                await post_weekly_standings(proactive_agent, client, semaphore)

            # Prune stale game data once per day
            if time.monotonic() - last_prune > 86400:
                _prune_stale_data()
                last_prune = time.monotonic()
                log.info("Pruned stale game data")

            # Reset proactive agent conversation between jobs to prevent
            # context pollution — each heartbeat post is independent
            if hasattr(proactive_agent, 'messages'):
                proactive_agent.messages.clear()

        except Exception as e:
            log.error("Heartbeat error: %s", e, exc_info=True)

        await asyncio.sleep(60)  # tick every 60 seconds
