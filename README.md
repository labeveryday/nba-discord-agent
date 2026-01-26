## Discord NBA Agent (Strands + `nba-stats-mcp`)

Created by [Du'An Lightfoot](https://duanlightfoot.com) | [@labeveryday](https://github.com/labeveryday)

This bot uses a **Strands Agent** wired to your published MCP server **`nba-stats-mcp`** (stdio). It answers NBA questions in Discord by calling MCP tools (scores, standings, player/team stats, etc.).

### Setup

- Create a virtualenv and install deps:

```bash
git clone https://github.com/labeveryday/nba-discord-agent.git
cd nba-discord-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Create a `.env` file (you can copy from `env.example`) and set `DISCORD_TOKEN`.

### Run

```bash
python discord_nba_agent.py
```

### How ongoing conversations work

The bot keeps **one Strands `Agent` per conversation**, so it retains message history in memory:

- **If you’re in a Discord thread**: the conversation is keyed by `thread_id`, so everyone in the thread shares context.
- **If you’re in a normal channel**: the conversation is keyed by `(channel_id, user_id)`, so different users don’t overwrite each other’s context in a busy channel.

### How to talk to it

- **Command**: `$nba <question>`
- **Mention**: `@Bot <question>` (starts/continues the same conversation scope)
- **Reply**: reply to one of the bot’s messages to continue the same conversation

## About

Built by **Du'An Lightfoot** ([@labeveryday](https://github.com/labeveryday))
- Website: [duanlightfoot.com](https://duanlightfoot.com)
- YouTube: [LabEveryday](https://youtube.com/@labeveryday)

---
