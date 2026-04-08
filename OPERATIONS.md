# NBA Discord Agent — Operations Guide

How the bot is wired together, where its data lives, and how the alerting and proactive scheduling actually work. Read this before debugging or making infrastructure changes.

---

## 1. Process model

There is **no cron, no systemd timer, and no external scheduler**. Everything runs inside one container:

```
docker container "nba-discord-agent"
└── python src/agent.py
    ├── discord.Client            # handles $nba, mentions, replies, DMs
    └── asyncio task: heartbeat_loop()
        ├── ticks every 60 s
        ├── reads HEARTBEAT.md (the schedule checklist)
        ├── asks the LLM "what should I do right now?"
        └── runs the matched action(s)
```

If the container is up, the bot is up. If the container is down, you get nothing.

---

## 2. Where data lives

| Thing | Inside container | On host |
|---|---|---|
| Source code | `/app/src/` | `./src/` (bind-baked into image at build time) |
| Heartbeat schedule | `/app/src/HEARTBEAT.md` | `./src/HEARTBEAT.md` |
| SQLite DB (heartbeat state, game threads, game state) | `/app/data/agent.db` | `/var/lib/docker/volumes/nba-discord-agent_agent-data/_data/agent.db` |
| Logs | stdout/stderr | `docker compose logs nba-discord-agent` |
| `.env` (secrets) | injected by `env_file` | `./.env` (gitignored) |

The DB path can be overridden with `SQLITE_DB_PATH` in `.env`. The volume `nba-discord-agent_agent-data` is a Docker named volume — it survives `docker compose down` and image rebuilds. To wipe state (e.g. force reposting today's recap), stop the container and run `docker volume rm nba-discord-agent_agent-data`.

To peek at the DB without stopping the bot:
```bash
sudo sqlite3 /var/lib/docker/volumes/nba-discord-agent_agent-data/_data/agent.db \
    'select * from heartbeat_log order by posted_at desc limit 20;'
```

---

## 3. The heartbeat loop

`src/heartbeat.py` runs as a background `asyncio` task. It is **reasoning-driven**, not hard-coded. The flow:

1. Tick (every `TICK_INTERVAL = 60` seconds).
2. Build a context dict: current ET time, today's NBA games, what's already been posted today, current scores.
3. If the context shows there might be work to do, call `_reason_about_actions()`. This sends `HEARTBEAT.md` plus the context to the LLM and asks for a JSON list of action names.
4. For each returned action, look it up in `ACTION_MAP` and run the executor.
5. Mark the action `posted` in the SQLite `heartbeat_log` table so it doesn't fire twice.

### The actions

| Action | Window (ET) | Executor | What it posts |
|---|---|---|---|
| `rise_and_grind` | 4:00–5:00 AM | `_exec_rise_and_grind` | Daily motivational wake-up message |
| `morning_recap` | 7:00 AM – noon | `_exec_morning_recap` | Last night's scores + Player of the Night |
| `gameday_preview` | ~11:00 AM | `_exec_gameday_preview` | Today's matchups, tip times, storylines |
| `game_threads` | ~11:00 AM | `_exec_game_threads` | Creates a Discord thread per game |
| `postgame_highlights` | every ~15 min during games | `_exec_postgame_highlights` | Box score summary when a game goes Final |
| `weekly_standings` | Mon ~10:00 AM | `_exec_weekly_standings` | Conference standings + streaks |

The full schedule is in `src/HEARTBEAT.md` — that file is the LLM's checklist. **If you change the schedule, edit `HEARTBEAT.md` and rebuild the image.** No code changes needed.

### Required Discord setup
- `HEARTBEAT_CHANNEL_ID` must be set in `.env` or the heartbeat is silently disabled. The bot logs `Heartbeat disabled (set HEARTBEAT_ENABLED=true and HEARTBEAT_CHANNEL_ID)` if it's missing.
- `GAME_THREAD_CHANNEL_ID` defaults to `HEARTBEAT_CHANNEL_ID`. The bot needs **Create Public Threads** permission in that channel.

---

## 4. Server alerts (the monitoring webhook)

These are **separate** from the NBA posts. They go to a different Discord webhook so they show up under a distinct identity ("NBA Agent Alerts") and you can route them to a private monitoring channel.

Set this in `.env`:
```bash
ALERTS_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
```

If this is unset, **all server alerts are silently dropped** — that is the most common reason "I stopped getting alerts."

### What fires an alert

| Alert | Level | When | Code |
|---|---|---|---|
| **Agent Online** | success (green) | Bot startup, after Discord login | `alert_startup()` in `agent.py` `on_ready` |
| **Heartbeat Actions** | info (blue) | Every cycle that actually executes ≥ 1 action | `alert_heartbeat_actions()` in `heartbeat.py` |
| **Heartbeat Error** | error (red) | An action raises an exception | `alert_heartbeat_error()` in `heartbeat.py` |
| **Ollama Unreachable** | error (red) | Ollama becomes unreachable (state transition only — does not spam) | `alert_ollama_check(False)` |
| **Ollama Recovered** | success (green) | Ollama becomes reachable again after being down | `alert_ollama_check(True)` |
| **Agent Error** | error (red) | Currently wired but rarely triggered (model crashes, etc.) | `alert_agent_error()` |

Two important quirks:

1. **`alert_ollama_check` only fires on state transitions.** The first call after startup just records the state — you will not see "Ollama Recovered" on boot. You will only see Ollama alerts if it actually goes down and comes back.
2. **`alert_heartbeat_actions` only fires when an action runs.** Idle ticks (most of the day) are silent. If no NBA games are scheduled and it's not 4–5 AM, you may go hours without any heartbeat-action alerts. That is normal — it does **not** mean the bot is broken.

### Verifying alerts manually

The fastest end-to-end test:
```bash
docker compose restart nba-discord-agent
```
You should immediately see an "Agent Online" embed in the alerts channel. If you don't:
- `grep ALERTS_WEBHOOK_URL .env` — is it set?
- Open the URL in a browser — Discord returns `{"code": 10015}` for deleted webhooks. If the webhook was deleted (or rotated and the URL never updated), recreate one in Channel → Edit Channel → Integrations → Webhooks, copy the new URL, paste into `.env`, then restart.
- `docker compose logs nba-discord-agent | grep -i "failed to send alert"` — look for HTTP errors.

### Why alerts may have stopped recently

When the bot was rebuilt as part of the refactor, the `HEARTBEAT.md` checklist file was missing from the image. The reasoning loop fell back to a stub prompt, the LLM returned `HEARTBEAT_OK` every cycle, and so `alert_heartbeat_actions` never had any actions to report. **No actions executed → no Discord posts → no action alerts.** Fixed by moving `HEARTBEAT.md` into `src/` so it ships with the source.

---

## 5. Model backend

Selected by `MODEL_PROVIDER` in `.env` (`ollama` | `anthropic` | `openai`). See README for the per-provider env vars.

**Ollama on this host:**
- Service: `systemd` unit `ollama.service`
- Listen: `0.0.0.0:11434` (set in drop-in `/etc/systemd/system/ollama.service.d/override.conf`)
- Model store: `/data/models/ollama` (set in same drop-in)
- Container reaches it via `host.docker.internal:11434` (mapped by `extra_hosts: host-gateway` in `docker-compose.yml`)

To restart Ollama: `sudo systemctl restart ollama`. The bot will fail-soft for ~30 seconds and then catch up on the next heartbeat tick — and you'll get an "Ollama Unreachable" → "Ollama Recovered" pair in the alerts channel.

---

## 6. Common operations

```bash
# Start / stop / restart
docker compose up -d
docker compose down
docker compose restart nba-discord-agent

# Tail live logs
docker compose logs -f nba-discord-agent

# Rebuild after code changes
docker compose build && docker compose up -d --force-recreate

# What did the heartbeat post today?
sudo sqlite3 /var/lib/docker/volumes/nba-discord-agent_agent-data/_data/agent.db \
    'select * from heartbeat_log where posted_at >= date("now");'

# In Discord — quick health check
$status

# Reset heartbeat state for today (will re-post recap/preview/etc.)
sudo sqlite3 /var/lib/docker/volumes/nba-discord-agent_agent-data/_data/agent.db \
    'delete from heartbeat_log where posted_at >= date("now");'
docker compose restart nba-discord-agent
```

---

## 7. Troubleshooting checklist

| Symptom | Likely cause | Check |
|---|---|---|
| No NBA posts at all | Container down, or `HEARTBEAT_CHANNEL_ID` unset | `docker compose ps` and `grep HEARTBEAT_CHANNEL_ID .env` |
| No morning recap | Today's posted state already marked, or LLM not parsing JSON | Check `heartbeat_log` table; `docker compose logs | grep -i "Posted morning recap\|reasoning"` |
| No server alerts at all | `ALERTS_WEBHOOK_URL` missing or webhook deleted | `grep ALERTS_WEBHOOK_URL .env`; recreate webhook if needed |
| Slow responses | Model too big for the GPU | Switch to `qwen3:4b` (`OLLAMA_MODEL=qwen3:4b` in `.env`) |
| Container crashes on boot with `FileNotFoundError: 'uvx'` | `NBA_MCP_USE_UVX=true` in `.env` but `uvx` isn't in the image | Set `NBA_MCP_USE_UVX=false` |
| `_load_heartbeat_md` warning in logs | `HEARTBEAT.md` not shipping in image | Confirm `src/HEARTBEAT.md` exists and Dockerfile copies `src/` |
| Ollama "unreachable" in `$status` | systemd service stopped, or not bound to `0.0.0.0` | `systemctl status ollama` and `systemctl cat ollama` |
