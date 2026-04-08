"""
Discord NBA bot powered by Strands Agents + nba-stats-mcp (MCP stdio).

Uses a local Ollama model (qwen3:8b) for inference — no cloud API keys needed.
Includes a heartbeat system for proactive posts (recaps, previews, scores).

Env vars:
- DISCORD_TOKEN (required)
- OLLAMA_HOST (optional; default: http://localhost:11434)
- OLLAMA_MODEL (optional; default: qwen3:8b-q4_K_M)
- NBA_MCP_USE_UVX (optional; "true" to run via `uvx nba-stats-mcp`)
- NBA_MCP_COMMAND (optional; default: nba-stats-mcp)
- NBA_MCP_ARGS (optional; extra args, shlex-split)
- HEARTBEAT_CHANNEL_ID (optional; Discord channel for proactive posts)
- GAME_THREAD_CHANNEL_ID (optional; Discord channel for game-day threads)
- HEARTBEAT_ENABLED (optional; default: true)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time

import discord
from dotenv import load_dotenv

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.tools.mcp import MCPClient
from strands_tools import current_time, rss

from alerts import alert_startup, alert_agent_error
from config import build_system_prompt
from heartbeat import heartbeat_loop
from hooks import NBAToolHooks
from models import build_model, current_model_id, current_provider
from utils import (
    ConversationCache,
    chunk_for_discord,
    is_max_tokens_exception,
    truthy,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


MAX_CONVERSATIONS = 100
RATE_LIMIT_SECONDS = 5


def build_mcp_client() -> MCPClient:
    use_uvx = truthy(os.getenv("NBA_MCP_USE_UVX"))
    extra_args = shlex.split(os.getenv("NBA_MCP_ARGS", ""))

    if use_uvx:
        command = "uvx"
        args = ["nba-stats-mcp", *extra_args]
    else:
        command = os.getenv("NBA_MCP_COMMAND", "nba-stats-mcp")
        args = [*extra_args]

    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(command=command, args=args)
        ),
        prefix="nba",
    )


def _heartbeat_enabled() -> bool:
    return truthy(os.getenv("HEARTBEAT_ENABLED", "true"))


def main() -> None:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN in environment.")

    mcp_client = build_mcp_client()
    # Semaphore(1) replaces the old Lock — heartbeat can check .locked() to yield
    ollama_semaphore = asyncio.Semaphore(1)
    rate_limit: dict[str, float] = {}

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        model = build_model()
        conversation_manager = SlidingWindowConversationManager(
            window_size=int(os.getenv("STRANDS_WINDOW_SIZE", "16")),
            per_turn=True,
        )

        conversations = ConversationCache(MAX_CONVERSATIONS)

        nba_hooks = NBAToolHooks()

        def make_agent() -> Agent:
            return Agent(
                model=model,
                tools=tools + [current_time, rss],
                system_prompt=build_system_prompt(),
                conversation_manager=conversation_manager,
                plugins=[nba_hooks],
                name="NBA Discord Agent",
                description="Answers NBA questions using nba-stats-mcp tools.",
            )

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True

        client = discord.Client(intents=intents)

        bot_start_time = time.monotonic()

        @client.event
        async def on_ready():
            print(f"Logged in as {client.user}")
            hb_enabled = _heartbeat_enabled() and bool(os.getenv("HEARTBEAT_CHANNEL_ID"))
            if hb_enabled:
                asyncio.create_task(
                    heartbeat_loop(make_agent, client, ollama_semaphore),
                    name="heartbeat",
                )
                print("Heartbeat loop started")
            else:
                print("Heartbeat disabled (set HEARTBEAT_ENABLED=true and HEARTBEAT_CHANNEL_ID)")
            alert_startup(f"{current_provider()}:{current_model_id()}", hb_enabled)

        @client.event
        async def on_message(message: discord.Message):
            if message.author == client.user:
                return

            content = (message.content or "").strip()
            if not content:
                return

            if client.user is None:
                return

            if content.startswith("$help") or content.startswith("$about"):
                await message.channel.send(
                    "**NBA Discord Agent** — powered by Strands + Ollama + nba-stats-mcp\n\n"
                    "**Ask me anything about the NBA:**\n"
                    "- `$nba <question>` — scores, stats, standings, schedules, player info\n"
                    "- `@Bot <question>` — mention me in any channel\n"
                    "- **Reply** to my messages to continue the conversation\n"
                    "- **DM me** directly — no prefix needed\n\n"
                    "**Proactive features** (automatic):\n"
                    "- Morning recap — yesterday's scores + Player of the Night\n"
                    "- Game-day preview — today's matchups and tip times\n"
                    "- Game threads — auto-created for each game\n"
                    "- Post-game highlights — box score summaries as games go Final\n"
                    "- Weekly standings — every Monday\n\n"
                    "**Utilities:**\n"
                    "- `$status` — health check (uptime, model, heartbeat state)\n"
                    "- `$help` — this message"
                )
                return

            if content.startswith("$status"):
                from heartbeat import _already_posted, _today_key, _yesterday_key, _week_key
                uptime_s = time.monotonic() - bot_start_time
                hours, rem = divmod(int(uptime_s), 3600)
                mins, _ = divmod(rem, 60)
                today = _today_key()
                yesterday = _yesterday_key()
                week = _week_key()

                provider = current_provider()
                model_id = current_model_id()

                # Quick reachability check (only meaningful for local Ollama)
                if provider == "ollama":
                    ollama_host = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
                    try:
                        from urllib.request import urlopen as _urlopen
                        with _urlopen(f"{ollama_host}/api/version", timeout=3):
                            backend_status = "connected"
                    except Exception:
                        backend_status = "unreachable"
                else:
                    backend_status = "remote"

                recap = "done" if _already_posted("recap", yesterday) else "pending"
                preview = "done" if _already_posted("preview", today) else "pending"
                threads = "done" if _already_posted("threads", today) else "pending"
                standings = "done" if _already_posted("standings", week) else "pending"
                convos = len(conversations)

                status_msg = (
                    f"```\n"
                    f"Uptime:       {hours}h {mins}m\n"
                    f"Model:        {provider}:{model_id} ({backend_status})\n"
                    f"Conversations: {convos} active\n"
                    f"\n"
                    f"Today's heartbeat:\n"
                    f"  Morning recap:  {recap}\n"
                    f"  Game preview:   {preview}\n"
                    f"  Game threads:   {threads}\n"
                    f"  Standings:      {standings}\n"
                    f"```"
                )
                await message.channel.send(status_msg)
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            is_nba_command = content.startswith("$nba")
            is_mention = client.user.mentioned_in(message)
            is_reply_to_bot = False
            if message.reference and message.reference.resolved:
                referenced = message.reference.resolved
                if isinstance(referenced, discord.Message) and referenced.author == client.user:
                    is_reply_to_bot = True

            if not (is_dm or is_nba_command or is_mention or is_reply_to_bot):
                return

            # Rate limiting per user (prune stale entries periodically)
            user_id = str(message.author.id)
            now = time.monotonic()
            last_request = rate_limit.get(user_id, 0)
            if now - last_request < RATE_LIMIT_SECONDS:
                await message.channel.send("Slow down! Try again in a few seconds.")
                return
            rate_limit[user_id] = now
            if len(rate_limit) > 500:
                cutoff = now - 60
                stale = [k for k, v in rate_limit.items() if v < cutoff]
                for k in stale:
                    del rate_limit[k]

            # Conversation scoping
            if is_dm:
                conversation_id = f"dm:{message.author.id}"
            elif isinstance(message.channel, discord.Thread):
                conversation_id = f"thread:{message.channel.id}"
            else:
                conversation_id = f"channel:{message.channel.id}:user:{message.author.id}"

            agent = conversations.get_or_create(conversation_id, make_agent)

            # Extract question
            question = content
            if is_nba_command:
                question = content[len("$nba"):].strip()
            elif is_mention:
                question = content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "")
                question = question.strip(" :,-\n\t")
            # DMs: use the full message as-is (no prefix needed)
            if not question:
                if is_dm:
                    await message.channel.send("Ask me anything about the NBA!")
                else:
                    await message.channel.send("Send a question after `$nba` or after the @mention.")
                return

            async with message.channel.typing():
                async with ollama_semaphore:
                    try:
                        result = await asyncio.to_thread(agent, question)
                        response_text = str(result).strip()
                    except Exception as e:
                        if is_max_tokens_exception(e):
                            brief = (
                                f"{question}\n\n"
                                "Respond VERY briefly (<= 8 bullet points). "
                                "If this is still too much, ask ONE clarifying question."
                            )
                            try:
                                result = await asyncio.to_thread(agent, brief)
                                response_text = str(result).strip()
                            except Exception:
                                response_text = "Something went wrong. Please try again."
                        else:
                            response_text = "Something went wrong. Please try again."

            # Reply to the original message (not DMs — they don't need it)
            first = True
            for part in chunk_for_discord(response_text):
                if first and not is_dm:
                    await message.reply(part, mention_author=False)
                    first = False
                else:
                    await message.channel.send(part)

        client.run(token)


if __name__ == "__main__":
    main()
