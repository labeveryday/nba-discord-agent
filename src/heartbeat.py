"""
Reasoning heartbeat system for proactive NBA Discord posts.

Instead of rigid if/else scheduling, the agent reads HEARTBEAT.md,
receives pre-computed context about the current state, and decides
what actions to take. Code handles data fetching and execution;
the LLM handles reasoning and content generation.
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
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    import feedparser
except ImportError:
    feedparser = None

import discord

from alerts import alert_heartbeat_actions, alert_heartbeat_error, alert_ollama_check

log = logging.getLogger("heartbeat")

ET = ZoneInfo("America/New_York")

# How often the heartbeat ticks (seconds). Most ticks short-circuit without LLM.
TICK_INTERVAL = 60
# Minimum seconds between LLM reasoning calls (avoid burning Ollama on empty ticks)
MIN_REASONING_INTERVAL = 300  # 5 minutes
# Agent call timeout
AGENT_TIMEOUT = 120

NBA_SCOREBOARD_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"

# Game hours window (ET)
GAME_HOURS_START = 12
GAME_HOURS_END = 2  # wraps midnight


# ---------------------------------------------------------------------------
# SQLite — context manager for all connections
# ---------------------------------------------------------------------------


def _db_path() -> str:
    return os.environ.get("SQLITE_DB_PATH", "/app/data/agent.db")


@contextmanager
def _db():
    conn = sqlite3.connect(_db_path())
    try:
        yield conn
    finally:
        conn.close()


def _init_db() -> None:
    Path(_db_path()).parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heartbeat_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                job_key TEXT NOT NULL UNIQUE,
                posted_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_threads (
                game_id TEXT PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_state (
                game_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hb_key ON heartbeat_log(job_key)")
        conn.commit()


def _already_posted(job_type: str, key: str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM heartbeat_log WHERE job_key = ?", (f"{job_type}:{key}",)).fetchone()
    return row is not None


def _mark_posted(job_type: str, key: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO heartbeat_log (job_type, job_key, posted_at) VALUES (?, ?, ?)",
            (job_type, f"{job_type}:{key}", int(time.time())),
        )
        conn.commit()


def _get_game_state(game_id: str) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT status FROM game_state WHERE game_id = ?", (game_id,)).fetchone()
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


def _get_thread_id(game_id: str) -> int | None:
    with _db() as conn:
        row = conn.execute("SELECT thread_id FROM game_threads WHERE game_id = ?", (game_id,)).fetchone()
    return row[0] if row else None


def _prune_stale_data(max_age_days: int = 7) -> None:
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
    return hour >= GAME_HOURS_START or hour < GAME_HOURS_END


# ---------------------------------------------------------------------------
# NBA API — direct calls, no LLM needed
# ---------------------------------------------------------------------------


def _fetch_scoreboard() -> list[dict]:
    try:
        req = Request(NBA_SCOREBOARD_URL, headers={"User-Agent": "nba-discord-agent/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("scoreboard", {}).get("games", [])
    except (URLError, json.JSONDecodeError, KeyError, OSError) as e:
        log.error("Failed to fetch NBA scoreboard: %s", e)
        return []


def _parse_games(games: list[dict]) -> list[dict]:
    parsed = []
    for g in games:
        away = g.get("awayTeam", {})
        home = g.get("homeTeam", {})
        status_text = str(g.get("gameStatusText", "")).strip()
        parsed.append(
            {
                "game_id": g.get("gameId", ""),
                "away_tri": away.get("teamTricode", "???"),
                "away_name": away.get("teamName", "Away"),
                "away_score": away.get("score", 0),
                "home_tri": home.get("teamTricode", "???"),
                "home_name": home.get("teamName", "Home"),
                "home_score": home.get("score", 0),
                "status_text": status_text,
                "is_final": "final" in status_text.lower(),
                "game_status": g.get("gameStatus", 0),
                "game_et": g.get("gameEt", ""),
            }
        )
    return parsed


NBA_RSS_FEEDS = [
    "https://www.espn.com/espn/rss/nba/news",
]


def _fetch_nba_headlines(limit: int = 5) -> list[dict]:
    """Fetch NBA headlines from RSS feeds. No LLM needed."""
    if not feedparser:
        return []
    headlines = []
    for url in NBA_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit]:
                headlines.append(
                    {
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", "")[:200],
                        "source": "ESPN",
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                    }
                )
        except Exception as e:
            log.warning("Failed to fetch RSS from %s: %s", url, e)
    return headlines[:limit]


def _format_headlines_for_context(headlines: list[dict]) -> str:
    if not headlines:
        return "  (no headlines available)"
    lines = []
    for h in headlines:
        lines.append(f"  - {h['title']} ({h['source']})")
    return "\n".join(lines)


def _format_games_for_context(games: list[dict]) -> str:
    if not games:
        return "  (none)"
    lines = []
    for g in games:
        lines.append(
            f"  {g['away_name']} ({g['away_tri']}) {g['away_score']}"
            f" @ {g['home_name']} ({g['home_tri']}) {g['home_score']}"
            f" — {g['status_text']} [game_id={g['game_id']}]"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------


def _heartbeat_channel_id() -> int | None:
    val = os.environ.get("HEARTBEAT_CHANNEL_ID")
    return int(val) if val else None


def _game_thread_channel_id() -> int | None:
    val = os.environ.get("GAME_THREAD_CHANNEL_ID") or os.environ.get("HEARTBEAT_CHANNEL_ID")
    return int(val) if val else None


def _chunk_for_discord(text: str, limit: int = 1900) -> list[str]:
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
    for part in _chunk_for_discord(text):
        await channel.send(part)


# ---------------------------------------------------------------------------
# Agent helper
# ---------------------------------------------------------------------------


async def _run_agent(
    agent, prompt: str, semaphore: asyncio.Semaphore, timeout: int = AGENT_TIMEOUT
) -> str | None:
    """Run agent with timeout, yielding if interactive users are active."""
    if semaphore.locked():
        log.info("Semaphore busy (user being served), skipping")
        return None
    try:
        async with semaphore:
            result = await asyncio.wait_for(
                asyncio.to_thread(agent, prompt),
                timeout=timeout,
            )
            return str(result).strip()
    except TimeoutError:
        log.error("Agent timed out after %ds", timeout)
        return None
    except Exception as e:
        log.error("Agent error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Context builder — pre-computes everything the reasoning agent needs
# ---------------------------------------------------------------------------


def _build_context(scoreboard_games: list[dict]) -> dict:
    """Build the full context dict the reasoning agent will receive."""
    now = _now_et()
    today = _today_key()
    yesterday = _yesterday_key()
    week = _week_key()

    parsed = _parse_games(scoreboard_games)
    finals = [g for g in parsed if g["is_final"]]

    # Find newly-final games (not yet reported)
    new_finals = []
    for g in finals:
        if g["game_id"] and _get_game_state(g["game_id"]) != "Final":
            new_finals.append(g)

    # Fetch NBA headlines (RSS, no LLM)
    headlines = _fetch_nba_headlines(limit=5)

    return {
        "now": now,
        "today": today,
        "yesterday": yesterday,
        "week": week,
        "hour": now.hour,
        "weekday": now.strftime("%A"),
        "weekday_num": now.weekday(),  # 0=Monday
        "all_games": parsed,
        "final_games": finals,
        "new_finals": new_finals,
        "recap_posted": _already_posted("recap", yesterday),
        "preview_posted": _already_posted("preview", today),
        "threads_posted": _already_posted("threads", today),
        "standings_posted": _already_posted("standings", week),
        "grind_posted": _already_posted("grind", today),
        "headlines": headlines,
        "is_game_hours": _is_game_hours(),
    }


def _has_potential_work(ctx: dict) -> bool:
    """Quick pre-check: is there ANYTHING that might need doing?

    This runs every tick (no LLM). If it returns False, we skip the
    reasoning call entirely. This is the performance gate.
    """
    # New final games → always worth checking
    if ctx["new_finals"]:
        return True

    # Morning recap window (7 AM - 12 PM) and not posted
    if 7 <= ctx["hour"] < 12 and not ctx["recap_posted"] and ctx["all_games"]:
        return True

    # Game-day preview window (9 AM - 2 PM) and not posted
    if 9 <= ctx["hour"] < 14 and not ctx["preview_posted"] and ctx["all_games"]:
        return True

    # Threads not created
    if 9 <= ctx["hour"] < 14 and not ctx["threads_posted"] and ctx["all_games"]:
        return True

    # Monday standings (8 AM - 2 PM)
    if ctx["weekday_num"] == 0 and 8 <= ctx["hour"] < 14 and not ctx["standings_posted"]:
        return True

    # Tuesday catch-up for standings
    if ctx["weekday_num"] == 1 and 8 <= ctx["hour"] < 14 and not ctx["standings_posted"]:
        return True

    # Rise and grind (4:15 AM - 5:00 AM)
    return 4 <= ctx["hour"] < 5 and not ctx["grind_posted"]


def _format_context_for_agent(ctx: dict) -> str:
    """Format the context dict into a readable text block for the LLM."""
    new_final_text = _format_games_for_context(ctx["new_finals"])
    all_games_text = _format_games_for_context(ctx["all_games"])

    finals = [g for g in ctx["all_games"] if g["is_final"]]
    in_progress = [g for g in ctx["all_games"] if not g["is_final"] and g["game_status"] == 2]
    scheduled = [g for g in ctx["all_games"] if g["game_status"] == 1]

    return f"""Current time: {ctx["now"].strftime("%I:%M %p ET, %A %B %-d, %Y")}
Day of week: {ctx["weekday"]} (weekday_num={ctx["weekday_num"]}, 0=Monday)
Game hours active: {ctx["is_game_hours"]}

NBA scoreboard right now ({len(ctx["all_games"])} games):
  Final: {len(finals)} | In progress: {len(in_progress)} | Scheduled: {len(scheduled)}
{all_games_text}

NOTE: The NBA scoreboard may still show last night's results if today's games haven't started.
If all games are Final and it's morning, these are LAST NIGHT'S results — use them for morning_recap.

Games that JUST went Final (not yet highlighted via postgame_highlights): {len(ctx["new_finals"])}
{new_final_text if ctx["new_finals"] else "  (none — all finals have been highlighted already)"}

What has been posted:
  morning_recap for {ctx["yesterday"]}: {"DONE" if ctx["recap_posted"] else "NOT DONE — needs posting if games are on the board"}
  gameday_preview for {ctx["today"]}: {"DONE" if ctx["preview_posted"] else "NOT DONE"}
  game_threads for {ctx["today"]}: {"DONE" if ctx["threads_posted"] else "NOT DONE"}
  weekly_standings for week {ctx["week"]}: {"DONE" if ctx["standings_posted"] else "NOT DONE"}
  rise_and_grind for {ctx["today"]}: {"DONE" if ctx["grind_posted"] else "NOT DONE"}

Top NBA headlines:
{_format_headlines_for_context(ctx["headlines"])}"""


# ---------------------------------------------------------------------------
# Action executors — one per action type
# ---------------------------------------------------------------------------


async def _exec_morning_recap(agent, client: discord.Client, semaphore: asyncio.Semaphore, ctx: dict) -> None:
    yesterday = ctx["yesterday"]
    channel = client.get_channel(_heartbeat_channel_id())
    if not channel:
        return

    game_summary = _format_games_for_context(ctx["final_games"])
    prompt = (
        f"Here are last night's NBA results ({yesterday}):\n\n"
        f"{game_summary}\n\n"
        f"Write a morning recap for Discord:\n"
        f"- Start with a greeting like 'Good morning! Here's what happened last night:'\n"
        f"- Each game on one line: **Winner score** - Loser score\n"
        f"- Pick a 'Player of the Night' based on the scores\n"
        f"- If you want detail on a top performer, use get_box_score with the game_id.\n"
        f"- Keep it concise and Discord-friendly. No IDs, no image URLs."
    )
    response = await _run_agent(agent, prompt, semaphore)
    if not response:
        return
    await _send_chunked(channel, f"🏀 **Morning Recap — {yesterday}**\n\n{response}")
    _mark_posted("recap", yesterday)
    log.info("Posted morning recap for %s", yesterday)


async def _exec_gameday_preview(
    agent, client: discord.Client, semaphore: asyncio.Semaphore, ctx: dict
) -> None:
    today = ctx["today"]
    channel = client.get_channel(_heartbeat_channel_id())
    if not channel:
        return

    game_summary = _format_games_for_context(ctx["all_games"])
    prompt = (
        f"Here are today's NBA games ({today}):\n\n"
        f"{game_summary}\n\n"
        f"Write a game-day preview for Discord:\n"
        f"- Each matchup: Away @ Home | note anything interesting\n"
        f"- Flag notable matchups (rivalry, playoff implications)\n"
        f"- Keep it concise. No IDs, no image URLs."
    )
    response = await _run_agent(agent, prompt, semaphore)
    if not response:
        return
    await _send_chunked(channel, f"📋 **Today's Games — {today}**\n\n{response}")
    _mark_posted("preview", today)
    log.info("Posted game-day preview for %s", today)


async def _exec_game_threads(client: discord.Client, ctx: dict) -> None:
    today = ctx["today"]
    channel_id = _game_thread_channel_id()
    if not channel_id:
        return
    channel = client.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    date_short = ctx["now"].strftime("%b %-d")
    created = 0

    for game in ctx["all_games"]:
        away = game["away_name"]
        home = game["home_name"]
        game_id = game["game_id"]

        # Parse game time from UTC to ET
        game_et = game.get("game_et", "")
        time_str = ""
        if "T" in game_et:
            try:
                utc_str = game_et.replace("Z", "+00:00")
                utc_dt = datetime.fromisoformat(utc_str)
                et_dt = utc_dt.astimezone(ET)
                h, m = et_dt.hour, et_dt.minute
                ampm = "AM" if h < 12 else "PM"
                dh = h if h <= 12 else h - 12
                if dh == 0:
                    dh = 12
                time_str = f"{dh}:{m:02d} {ampm} ET"
            except (ValueError, IndexError):
                pass

        thread_name = f"{away} @ {home} | {date_short}"
        if time_str:
            thread_name += f" | {time_str}"

        try:
            thread = await channel.create_thread(
                name=thread_name[:100],
                type=discord.ChannelType.public_thread,
                auto_archive_duration=1440,
            )
            if game_id:
                _save_thread(game_id, thread.id)
            await thread.send(f"🏀 **{away} @ {home}** — Game thread! Discuss the game here.")
            created += 1
        except discord.HTTPException as e:
            log.error("Failed to create thread for %s @ %s: %s", away, home, e)

    _mark_posted("threads", today)
    log.info("Created %d game threads for %s", created, today)


async def _exec_postgame_highlights(
    agent,
    client: discord.Client,
    semaphore: asyncio.Semaphore,
    ctx: dict,
    game_ids: list[str] | None = None,
) -> None:
    channel_id = _heartbeat_channel_id()
    if not channel_id:
        return

    # Use game_ids from reasoning agent if provided, otherwise use all new finals
    targets = ctx["new_finals"]
    if game_ids:
        id_set = set(game_ids)
        targets = [g for g in targets if g["game_id"] in id_set]

    # Batch mode: if 2-3 games, combine into one post
    if 2 <= len(targets) <= 3:
        await _exec_postgame_batch(agent, client, semaphore, targets, channel_id)
        return

    # Individual posts
    for game in targets:
        game_id = game["game_id"]
        away = game["away_name"]
        home = game["home_name"]
        away_score = game["away_score"]
        home_score = game["home_score"]

        log.info("Generating highlight for %s %s @ %s %s", away, away_score, home, home_score)

        prompt = (
            f"Use get_box_score with game_id={game_id} to get the box score. "
            f"The final score was {away} {away_score} - {home} {home_score}. "
            f"Write a 3-sentence post-game highlight:\n"
            f"- Line 1: Final score with winner bolded\n"
            f"- Line 2: Top performer from each team (name + pts/reb/ast)\n"
            f"- Line 3: One notable stat or storyline\n"
            f"No IDs, no image URLs."
        )
        highlight = await _run_agent(agent, prompt, semaphore)
        if not highlight:
            log.warning("Highlight failed for game_id=%s", game_id)
            continue

        target = None
        thread_id = _get_thread_id(game_id)
        if thread_id:
            target = client.get_channel(thread_id)
        if not target:
            target = client.get_channel(channel_id)

        if target:
            await _send_chunked(target, f"🚨 **Final Score**\n\n{highlight}")
            _set_game_state(game_id, "Final")
            log.info("Posted highlight for %s @ %s", away, home)
        else:
            log.error("No channel for highlight (channel_id=%s)", channel_id)


async def _exec_postgame_batch(
    agent,
    client: discord.Client,
    semaphore: asyncio.Semaphore,
    games: list[dict],
    channel_id: int,
) -> None:
    """Post a batched highlight for 2-3 games in one message."""
    game_lines = []
    ids = []
    for g in games:
        game_lines.append(
            f"- {g['away_name']} {g['away_score']} @ {g['home_name']} {g['home_score']}"
            f" (game_id={g['game_id']})"
        )
        ids.append(g["game_id"])

    prompt = (
        f"These games just finished:\n"
        f"{''.join(chr(10) + line for line in game_lines)}\n\n"
        f"For each game, use get_box_score to get the box score.\n"
        f"Then write a combined post-game update:\n"
        f"- For each game: **Winner score** - Loser score | top performer (pts/reb/ast)\n"
        f"- End with a 'Best performance of the night' pick\n"
        f"Keep it compact. No IDs, no image URLs."
    )

    response = await _run_agent(agent, prompt, semaphore)
    if not response:
        return

    channel = client.get_channel(channel_id)
    if channel:
        await _send_chunked(channel, f"🚨 **Final Scores**\n\n{response}")
        for gid in ids:
            _set_game_state(gid, "Final")
        log.info("Posted batched highlight for %d games", len(ids))


async def _exec_weekly_standings(
    agent, client: discord.Client, semaphore: asyncio.Semaphore, ctx: dict
) -> None:
    week = ctx["week"]
    channel = client.get_channel(_heartbeat_channel_id())
    if not channel:
        return

    prompt = (
        "Use get_standings to get current NBA standings. "
        "Format them for Discord:\n"
        "- Split by Eastern and Western conference\n"
        "- Show: Rank | Team | W-L | Win% | GB | Streak\n"
        "- Use a clean table format\n"
        "- Flag teams on 5+ game win/loss streaks\n"
        "Keep it clean and readable. No IDs."
    )
    response = await _run_agent(agent, prompt, semaphore)
    if not response:
        return
    await _send_chunked(channel, f"📊 **Weekly Standings — {week}**\n\n{response}")
    _mark_posted("standings", week)
    log.info("Posted weekly standings for %s", week)


async def _exec_rise_and_grind(
    agent, client: discord.Client, semaphore: asyncio.Semaphore, ctx: dict
) -> None:
    today = ctx["today"]
    channel = client.get_channel(_heartbeat_channel_id())
    if not channel:
        return

    prompt = (
        "Write a motivational wake-up message for 4:30 AM. Pick ONE of these styles "
        "and channel their energy: Jocko Willink, Joe Rogan, Denzel Washington, Les Brown, "
        "David Goggins, Eric Thomas, Tony Robbins, or Kobe Bryant. "
        "Rotate the style each day. Do not say who you are channeling.\n\n"
        "The message MUST include:\n"
        "- Get up right now. No snooze. No excuses.\n"
        "- Give thanks for another day. Gratitude first.\n"
        "- Do those push-ups. Get the workout in.\n"
        "- Put in work today. Outwork everyone.\n"
        "- Create that YouTube video and post it. The content is not going to create itself.\n\n"
        "Tone: direct, intense, like a coach who believes in you. "
        "Under 200 words. No hashtags. No emojis except one fire emoji at the end."
    )

    response = await _run_agent(agent, prompt, semaphore)
    if not response:
        return
    await _send_chunked(channel, response)
    _mark_posted("grind", today)
    log.info("Posted rise and grind for %s", today)


# Action dispatch table
ACTION_MAP = {
    "morning_recap": _exec_morning_recap,
    "gameday_preview": _exec_gameday_preview,
    "game_threads": _exec_game_threads,
    "postgame_highlights": _exec_postgame_highlights,
    "weekly_standings": _exec_weekly_standings,
    "rise_and_grind": _exec_rise_and_grind,
}


# ---------------------------------------------------------------------------
# Reasoning engine — the core of the heartbeat
# ---------------------------------------------------------------------------


def _load_heartbeat_md() -> str:
    """Load HEARTBEAT.md — looks next to this file, then /app/src, then CWD."""
    here = Path(__file__).parent / "HEARTBEAT.md"
    for path in [str(here), "/app/src/HEARTBEAT.md", "/app/HEARTBEAT.md", "HEARTBEAT.md"]:
        p = Path(path)
        if p.exists():
            return p.read_text()
    log.warning("HEARTBEAT.md not found, using fallback")
    return "Decide what actions to take based on the context provided."


async def _reason_about_actions(agent, semaphore: asyncio.Semaphore, ctx: dict) -> list[dict]:
    """Ask the LLM to decide what actions to take.

    Returns a list of action dicts, e.g. [{"action": "morning_recap"}, ...]
    Returns empty list if nothing to do or on error.
    """
    heartbeat_md = _load_heartbeat_md()
    context_text = _format_context_for_agent(ctx)

    prompt = (
        f"{heartbeat_md}\n\n"
        f"---\n\n"
        f"## Current State\n\n"
        f"{context_text}\n\n"
        f"---\n\n"
        f"Based on the checklist and current state above, what actions should be taken right now?\n"
        f"Respond with a JSON array of action objects, or HEARTBEAT_OK if nothing is needed."
    )

    response = await _run_agent(agent, prompt, semaphore, timeout=60)
    if not response:
        alert_ollama_check(False)
        return []

    # Check for HEARTBEAT_OK
    if "HEARTBEAT_OK" in response:
        log.info("Heartbeat: HEARTBEAT_OK")
        return []

    # Parse JSON actions from response
    try:
        start = response.find("[")
        end = response.rfind("]") + 1
        if start == -1 or end == 0:
            log.warning("Heartbeat reasoning: no JSON array in response: %s", response[:200])
            return []
        actions = json.loads(response[start:end])
        if not isinstance(actions, list):
            return []
        # Validate each action
        valid = []
        for a in actions:
            if isinstance(a, dict) and a.get("action") in ACTION_MAP:
                valid.append(a)
            else:
                log.warning("Heartbeat: unknown action: %s", a)
        log.info("Heartbeat decided: %s", [a["action"] for a in valid])
        return valid
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("Heartbeat reasoning: JSON parse failed: %s — response: %s", e, response[:200])
        return []


# ---------------------------------------------------------------------------
# Main heartbeat loop
# ---------------------------------------------------------------------------


async def heartbeat_loop(
    make_agent,
    client: discord.Client,
    semaphore: asyncio.Semaphore,
) -> None:
    """Main heartbeat loop — pre-checks context, reasons when needed, executes actions."""
    _init_db()
    await client.wait_until_ready()
    await asyncio.sleep(10)

    proactive_agent = make_agent()
    last_reasoning = 0.0
    last_prune = time.monotonic()

    log.info("Heartbeat loop started (reasoning mode)")

    while not client.is_closed():
        try:
            # Fetch scoreboard (direct API, fast, no LLM)
            scoreboard = await asyncio.to_thread(_fetch_scoreboard)
            ctx = _build_context(scoreboard)

            # Fast pre-check: anything potentially actionable?
            if not _has_potential_work(ctx):
                await asyncio.sleep(TICK_INTERVAL)
                continue

            # Throttle reasoning calls
            elapsed = time.monotonic() - last_reasoning
            if elapsed < MIN_REASONING_INTERVAL:
                await asyncio.sleep(TICK_INTERVAL)
                continue

            log.info("Potential work detected, invoking reasoning agent...")
            cycle_start = time.monotonic()

            # Ask the LLM: what should we do?
            actions = await _reason_about_actions(proactive_agent, semaphore, ctx)
            last_reasoning = time.monotonic()

            if not actions:
                alert_ollama_check(True)  # reasoning worked = Ollama is up
                await asyncio.sleep(TICK_INTERVAL)
                continue

            # Execute each action
            executed = []
            for action in actions:
                action_name = action["action"]
                log.info("Executing action: %s", action_name)

                try:
                    if action_name == "game_threads":
                        await _exec_game_threads(client, ctx)
                    elif action_name == "postgame_highlights":
                        game_ids = action.get("game_ids")
                        await _exec_postgame_highlights(proactive_agent, client, semaphore, ctx, game_ids)
                    else:
                        executor = ACTION_MAP[action_name]
                        await executor(proactive_agent, client, semaphore, ctx)
                    executed.append(action_name)
                except Exception as e:
                    log.error("Action %s failed: %s", action_name, e, exc_info=True)
                    alert_heartbeat_error(f"Action {action_name}: {e}")

            # Send summary alert
            duration = time.monotonic() - cycle_start
            alert_heartbeat_actions(executed, duration)
            alert_ollama_check(True)

            # Reset conversation between reasoning cycles
            if hasattr(proactive_agent, "messages"):
                proactive_agent.messages.clear()

            # Prune stale data daily
            if time.monotonic() - last_prune > 86400:
                _prune_stale_data()
                last_prune = time.monotonic()

        except Exception as e:
            log.error("Heartbeat error: %s", e, exc_info=True)

        await asyncio.sleep(TICK_INTERVAL)
