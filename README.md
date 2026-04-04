## Discord NBA Agent (Strands + Ollama + `nba-stats-mcp`)

Created by [Du'An Lightfoot](https://duanlightfoot.com) | [@labeveryday](https://github.com/labeveryday)

This bot uses a **Strands Agent** with a local **Ollama** model (`qwen3:8b`) wired to your published MCP server **`nba-stats-mcp`** (stdio). It answers NBA questions in Discord by calling MCP tools (scores, standings, player/team stats, etc.). No cloud API keys required — runs entirely on your local network.

Includes a **heartbeat system** inspired by [OpenClaw](https://github.com/openclaw/openclaw) that makes the bot proactive — it posts morning recaps, game-day previews, post-game highlights, and weekly standings on a schedule without anyone asking.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Ollama](https://ollama.com/) running on the host with `qwen3:8b-q4_K_M` pulled
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))

### Setup

1. Clone the repo:

```bash
git clone https://github.com/labeveryday/nba-discord-agent.git
cd nba-discord-agent
```

2. Create your `.env` file:

```bash
cp env.example .env
# Edit .env and set DISCORD_TOKEN
chmod 600 .env
```

3. Make sure Ollama is accessible to Docker containers. Add an override so Ollama listens on all interfaces:

```bash
sudo systemctl edit ollama.service
```

Add:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Then restart and firewall it:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama

# Allow only localhost and Docker, block everything else
sudo iptables -A INPUT -p tcp --dport 11434 -s 127.0.0.0/8 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 11434 -s 172.16.0.0/12 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 11434 -j DROP
```

4. Build and run:

```bash
docker compose build
docker compose up -d
```

5. Check logs:

```bash
docker compose logs -f
```

### How to talk to it

- **Command**: `$nba <question>`
- **Mention**: `@Bot <question>` (starts/continues the same conversation scope)
- **Reply**: reply to one of the bot's messages to continue the same conversation
- **Help**: `$help`

### How ongoing conversations work

The bot keeps **one Strands Agent per conversation**, so it retains message history in memory:

- **In a Discord thread**: the conversation is keyed by `thread_id`, so everyone in the thread shares context.
- **In a normal channel**: the conversation is keyed by `(channel_id, user_id)`, so different users don't overwrite each other's context.

### Heartbeat (proactive features)

The bot includes an OpenClaw-inspired heartbeat system that runs as a background task alongside the Discord listener. When enabled, it proactively posts NBA content on a schedule:

| Feature | Schedule | What it does |
|---------|----------|-------------|
| Morning Recap | Daily, 9 AM ET | Summarizes last night's games with scores and top performers |
| Game-Day Preview | Daily, 11 AM ET | Lists today's matchups, tip times, and storylines to watch |
| Game-Day Threads | Daily, 11 AM ET | Creates a Discord thread per game for organized discussion |
| Post-Game Highlights | Every 15 min during games | Detects newly-finished games and posts box score highlights |
| Weekly Standings | Monday, 10 AM ET | Posts current conference standings with streaks |

**How it works:**
- A background `asyncio` task ticks every 60 seconds and checks the current time against the schedule
- Each job is idempotent — a SQLite database tracks what's already been posted so nothing duplicates
- The heartbeat **yields to interactive users** — if someone is asking a question, proactive posts wait until the model is free
- Game state (which games are final, which threads exist) is persisted in SQLite and survives container restarts

**To enable heartbeat**, add these to your `.env`:

```bash
# Required: the channel where recaps, previews, and standings are posted
HEARTBEAT_CHANNEL_ID=123456789012345678

# Optional: channel where game-day threads are created (defaults to HEARTBEAT_CHANNEL_ID)
GAME_THREAD_CHANNEL_ID=123456789012345678

# Optional: disable heartbeat entirely (default: true)
HEARTBEAT_ENABLED=true
```

Get channel IDs by enabling Developer Mode in Discord (Settings > Advanced) and right-clicking a channel > Copy Channel ID.

### Container security

| Layer | Protection |
|-------|-----------|
| Non-root user | Runs as `agent` user with no login shell |
| Read-only filesystem | Only `/app/data` (named volume) and `/tmp` (tmpfs) are writable |
| All capabilities dropped | `cap_drop: ALL`, `no-new-privileges` |
| Named volume | Docker-managed storage — no host filesystem access |
| No GPU passthrough | Makes HTTP calls to Ollama on host |
| No host volumes | No bind mounts to host directories |
| Resource limits | 768MB RAM, 1 CPU |
| Rate limiting | 5 second per-user cooldown |

### Configuration

All configuration is via environment variables (see `env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | Yes | — | Discord bot token |
| `OLLAMA_HOST` | No | `http://host.docker.internal:11434` | Ollama server address |
| `OLLAMA_MODEL` | No | `qwen3:8b-q4_K_M` | Ollama model to use |
| `NBA_MCP_COMMAND` | No | `nba-stats-mcp` | MCP server command |
| `NBA_MCP_ARGS` | No | — | Extra args for MCP server |
| `NBA_MCP_USE_UVX` | No | `false` | Run MCP server via `uvx` |
| `HEARTBEAT_CHANNEL_ID` | No | — | Channel for proactive posts |
| `GAME_THREAD_CHANNEL_ID` | No | `HEARTBEAT_CHANNEL_ID` | Channel for game-day threads |
| `HEARTBEAT_ENABLED` | No | `true` | Enable/disable heartbeat |

### Architecture

```
┌─────────────────────────────────────────┐
│           Docker Container               │
│                                          │
│  Discord.py Event Loop                   │
│  ├── on_message() → Interactive Agent    │
│  │   (per-conversation, user-triggered)  │
│  │                                       │
│  └── Heartbeat Task → Proactive Agent    │
│      (background, schedule-triggered)    │
│                                          │
│  Semaphore(1) ensures one Ollama call    │
│  at a time — interactive always wins     │
│                                          │
│  SQLite (/app/data/agent.db)             │
│  ├── heartbeat_log (posted tracking)     │
│  ├── game_threads (game → thread map)    │
│  └── game_state (final score tracking)   │
└──────────────┬───────────────────────────┘
               │
     ┌─────────┴─────────┐
     ▼                   ▼
  Discord API      Ollama (host)
                   + nba-stats-mcp
```

## About

Built by **Du'An Lightfoot** ([@labeveryday](https://github.com/labeveryday))
- Website: [duanlightfoot.com](https://duanlightfoot.com)
- YouTube: [LabEveryday](https://youtube.com/@labeveryday)

---
