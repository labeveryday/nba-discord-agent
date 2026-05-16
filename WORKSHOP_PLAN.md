# Workshop Plan: "Building AI Agents with Strands"

## Stanford University | 1 Hour | 60-80 Students

---

## Overview

A hands-on workshop where Stanford students and alum build an AI agent on Akamai Cloud (Linode) GPUs using the Strands Agents SDK. Students learn the three core patterns behind every production AI agent: **tool use**, **conversation memory**, and **autonomous reasoning loops**. The NBA Discord Agent serves as the reference implementation they explore after the workshop.

**Student environment:** VS Code in the browser (code-server pods on LKE, password-protected). No Jupyter. Agents are software — students should see real project structure, real files, and real terminal output.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    STUDENTS (Browser)                     │
│              workshop.akamai-devrel.dev                   │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS
              ┌────────▼────────┐
              │  NodeBalancer   │
              │  (Linode LB)    │
              └────────┬────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              LKE Cluster (Linode Kubernetes Engine)       │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │         CPU Node Pool (3x Dedicated 32GB)        │    │
│  │                                                   │    │
│  │  ┌──────────────────────────────────────────┐    │    │
│  │  │  80x code-server Pods (1GB RAM each)     │    │    │
│  │  │  Each pod:                                │    │    │
│  │  │   - Unique password (generated at deploy) │    │    │
│  │  │   - Workshop repo pre-cloned              │    │    │
│  │  │   - Python + deps + nba-stats-mcp         │    │    │
│  │  │   - MCP server runs locally (stdio)       │    │    │
│  │  └───────────────────────┬──────────────────┘    │    │
│  │                          │                        │    │
│  │  Ingress (path-based routing):                   │    │
│  │   /s01/ → pod-01, /s02/ → pod-02, ... /s80/     │    │
│  │                                                   │    │
│  └──────────────────────────┼────────────────────────┘    │
│                                    │ http://vllm:8000/v1  │
│  ┌─────────────────────────────────▼─────────────────┐    │
│  │       GPU Node Pool (5x RTX 4000 Ada Small)       │    │
│  │                                                    │    │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐    │    │
│  │  │  vLLM      │ │  vLLM      │ │  vLLM      │... │    │
│  │  │  Pod 1     │ │  Pod 2     │ │  Pod 3     │    │    │
│  │  │  Qwen3 8B  │ │  Qwen3 8B  │ │  Qwen3 8B  │    │    │
│  │  │ continuous │ │ continuous │ │ continuous │    │    │
│  │  │  batching  │ │  batching  │ │  batching  │    │    │
│  │  └────────────┘ └────────────┘ └────────────┘    │    │
│  │  + 2 more (5 total, ~16 students each)            │    │
│  │                                                    │    │
│  │  K8s Service: vllm (load balances across pods)    │    │
│  │  OpenAI-compatible API at /v1/chat/completions    │    │
│  └────────────────────────────────────────────────────┘    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### Why VS Code Server, Not Jupyter

| Factor | VS Code (code-server) | Jupyter |
|--------|----------------------|---------|
| **Mental model** | "Agents are software" — files, modules, terminal | "AI is an experiment" — cells, outputs |
| **Project structure** | Students see real files, real imports, real layout | Hidden behind notebook cells |
| **Terminal output** | Colored hooks output (tool calls, reasoning) renders naturally | Text in a cell output box |
| **Student familiarity** | Stanford CS students live in VS Code | Jupyter is for ML homework |
| **Authenticity** | This is how you actually build agents | Notebooks aren't production |
| **Pacing** | Numbered scripts: `01_first_agent.py`, `02_add_tools.py`... | Cell-by-cell execution |

### Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| **5 shared GPUs with vLLM, not 1:1** | 5 vLLM pods with continuous batching + PagedAttention. Each handles ~16 students dynamically — no fixed slot limits. Handles bursty MCP tool contexts efficiently. |
| **Direct code-server pods, no Coder** | Simpler, less attack surface. 80 pods behind an Ingress with path-based routing. No admin UI to secure. Each pod has its own password. |
| **Browser-only access** | No SSH, no local setup. Student gets a URL + password on a printed card, VS Code opens in their browser. |
| **LKE, not raw VMs** | K8s team can own this. Helm charts for everything. Repeatable for future workshops. |
| **MCP server runs locally per workspace (stdio)** | `nba-stats-mcp` installed via pip, launched as a subprocess. Same pattern as the production agent. No shared MCP pod needed. |
| **No network restrictions** | 1-hour workshop, cluster destroyed after. Keep it simple. |

### Cost Estimate

| Resource | Spec | Qty | $/hr | Total |
|----------|------|-----|------|-------|
| RTX 4000 Ada Small | 1 GPU, 4 vCPU, 16GB | 5 | $0.52 | $2.60/hr |
| Dedicated 32GB CPU | 16 vCPU, 32GB | 3 | ~$0.29 | $0.87/hr |
| NodeBalancer | Standard | 1 | ~$0.015 | $0.015/hr |
| **Workshop runtime** (2 hrs w/ buffer) | | | | **~$7** |
| **Prep day** (24 hrs up) | | | | **~$84** |
| **Total** | | | | **~$91** |

---

## Security Model

### Access Flow

```
Student gets printed card at their seat:
  ┌─────────────────────────────────────┐
  │  Your Workshop Environment          │
  │                                     │
  │  URL: workshop.akamai-devrel.dev/s42│
  │  Password: nba-s42-7fk2            │
  │                                     │
  │  [QR CODE]                          │
  └─────────────────────────────────────┘

Student scans QR or types URL → HTTPS → code-server password prompt → VS Code
```

- No user accounts, no SSO, no admin dashboard
- Each workspace is an independent pod with its own random password
- Passwords generated at provision time by `generate-pods.sh`, printed to cards
- No password reuse across workshops

### Network

No network policies. This is a 1-hour workshop — the cluster is destroyed 30 minutes after it ends. Complexity here doesn't help the learning objective.

Workspace pods have full internet access (needed for `nba-stats-mcp` to call NBA APIs). Each pod runs its own MCP server as a local stdio subprocess — no shared MCP pod.

### Pod Security

Every workspace pod runs with:
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: false    # students need to edit files
  capabilities:
    drop: ["ALL"]
resources:
  limits:
    memory: "1Gi"
    cpu: "500m"
  requests:
    memory: "512Mi"
    cpu: "250m"
```

### What's Exposed to the Internet

| Component | Exposed? | How |
|-----------|----------|-----|
| Workspace URLs (`/s01/` - `/s80/`) | Yes | HTTPS via NodeBalancer, password-protected |
| vLLM API (port 8000) | No | ClusterIP only |
| MCP Server | N/A | Runs locally in each workspace pod (stdio) |
| K8s API | No | Linode restricts to authorized kubeconfigs |
| Admin/management UI | None exists | No Coder = no admin UI to attack |

### Secrets Management

| Secret | Storage | Access |
|--------|---------|--------|
| Linode API token | `terraform.tfvars` (gitignored) or env var | Du'An only |
| Student workspace passwords | K8s Secrets, generated by `generate-pods.sh` | Du'An + DA (via printed cards) |
| TLS cert | cert-manager or Linode-managed on NodeBalancer | Automated |

### Post-Workshop Cleanup

- `teardown.sh` destroys the entire cluster within 30 minutes of workshop ending
- All workspace passwords become invalid when pods are deleted
- DNS record removed or pointed to "workshop ended" page
- No persistent data survives teardown (no volumes, no databases)

### Threat Mitigations

| Threat | Mitigation |
|--------|-----------|
| Student accesses another workspace | Each pod has unique password; no cross-pod network access |
| Student hits vLLM directly for personal use | vLLM is ClusterIP only, no Ingress — only reachable from inside cluster |
| Student tries kubectl | No kubeconfig in workspace; K8s API requires authorized kubeconfig |
| Student installs rogue packages | Pre-built image, 1-hour window, cluster destroyed after |
| Student escapes pod | Non-root, no capabilities, no privilege escalation |
| Shared URL leaks after workshop | Cluster destroyed within 30 min; passwords are one-time |
| Student tries to reach cloud metadata | Pod security context, non-root user, 1-hour window |

---

## Repos to Create

### Repo 1: `akamai-devrel/ai-agents-workshop` (Public)

The workshop itself. Students fork/clone this. Pre-loaded into each workspace pod.

```
ai-agents-workshop/
│
├── README.md                          # Workshop overview, setup, what you'll learn
│
├── slides/
│   └── building-ai-agents.pdf         # Du'An's 10-min presentation deck
│
├── docs/                              # "Kubernetes the Hard Way" style guide
│   ├── README.md                      # Table of contents with links
│   ├── 00-setup.md                    # Verify environment, project tour
│   ├── 01-your-first-agent.md         # What IS an agent? Create one.
│   ├── 02-tool-use.md                 # How tools work. THE key section.
│   ├── 03-conversation-memory.md      # Why memory matters, sliding windows
│   ├── 04-autonomous-reasoning.md     # The heartbeat pattern
│   └── 05-whats-next.md              # Reference repo, take-home, resources
│                                      # Each doc: concept explanation, "Run It"
│                                      # command, expected output, "Try This",
│                                      # recap. Du'An projects this on screen.
│                                      # Students read in VS Code or GitHub.
│
├── workshop/                          # The guided workshop — numbered scripts
│   │
│   ├── 00_verify.py                   # Health check: confirms LLM (vLLM), MCP, Strands
│   │                                  # Students run first: python workshop/00_verify.py
│   │
│   ├── 01_first_agent.py              # Section 1: An Agent That Talks
│   │                                  # - Creates a basic Strands agent
│   │                                  # - Students chat with it
│   │                                  # - Shows it CAN'T answer real-time questions
│   │                                  # - Big comment block at top explains the concepts
│   │                                  # - Ends with: "Run 02_add_tools.py next"
│   │
│   ├── 02_add_tools.py                # Section 2: An Agent That Acts
│   │                                  # - Imports NBA tools (get_scoreboard, etc.)
│   │                                  # - Adds ToolDisplayHook for colored output
│   │                                  # - Students ask NBA questions, watch tool calls
│   │                                  # - Includes "TRY THIS" prompts to experiment
│   │
│   ├── 03_add_memory.py               # Section 3: An Agent That Remembers
│   │                                  # - First: shows the no-memory problem
│   │                                  # - Then: adds SlidingWindowConversationManager
│   │                                  # - Students have multi-turn conversations
│   │
│   ├── 04_heartbeat.py                # Section 4: An Agent That Thinks Alone
│   │                                  # - Loads live game context
│   │                                  # - Student EDITS the heartbeat_criteria string
│   │                                  # - Triggers one heartbeat tick
│   │                                  # - Agent reasons about what to post
│   │                                  # - Students modify criteria, re-run, compare
│   │
│   └── solutions/                     # Completed versions of each script
│       ├── 01_first_agent_done.py     # If a student is stuck, they can run these
│       ├── 02_add_tools_done.py       # to catch up to the group
│       ├── 03_add_memory_done.py
│       └── 04_heartbeat_done.py
│
├── src/
│   ├── agent.py                       # Minimal agent factory (< 50 lines)
│   │                                  # Creates a Strands agent with configurable
│   │                                  # tools, memory, and hooks
│   │
│   ├── tools/
│   │   ├── mcp.py                     # build_mcp_client() — connects to nba-stats-mcp
│   │   │                              # via stdio (same pattern as production agent)
│   │   └── demo.py                    # Mock tools for demo mode (no MCP needed)
│   │
│   ├── heartbeat.py                   # Simplified heartbeat function
│   │                                  # Takes context + criteria prompt →
│   │                                  # returns agent's reasoning + decision
│   │                                  # (no async loop, no Discord — just the
│   │                                  #  reasoning pattern, callable from terminal)
│   │
│   ├── hooks.py                       # Tool call display hook
│   │                                  # Prints colored terminal output:
│   │                                  # 🔧 Tool Call: get_scoreboard(date="20260505")
│   │                                  # 📊 Result: GSW 108 - LAL 102 (Final)...
│   │                                  # Makes the "agent chose to call this" visible
│   │
│   └── config.py                      # Model config: vLLM endpoint (workshop),
│                                      # Ollama (local dev), or demo mode
│                                      # Uses OpenAIModel for vLLM, OllamaModel for local
│
├── reference/
│   └── README.md                      # "You just learned the 3 core patterns.
│                                      #  Here's a production agent that uses
│                                      #  all of them. See: nba-discord-agent repo"
│                                      #  Maps workshop concepts → production code
│
├── extend/                            # Take-home challenges
│   ├── 01-deploy-to-discord.md        # Wire your agent to a Discord bot
│   ├── 02-add-persistence.md          # Add SQLite state (idempotent heartbeat)
│   ├── 03-multi-agent.md              # Agents-as-tools orchestration
│   └── 04-your-own-domain.md          # Replace NBA with any domain + MCP server
│
├── requirements.txt                   # strands-agents, strands-agents-tools, etc.
├── Makefile                           # make verify (health check for student env)
└── .vscode/
    └── settings.json                  # Pre-configured: Python path, terminal defaults
                                       # So VS Code "just works" when they open it
```

### Repo 2: `akamai-devrel/ai-agents-workshop-infra` (Private)

K8s team owns this after initial design.

```
ai-agents-workshop-infra/
│
├── README.md                          # Architecture diagram, security model, runbook
│
├── terraform/
│   ├── main.tf                        # LKE cluster: CPU pool + GPU pool
│   ├── variables.tf                   # Region, node counts, instance types
│   └── outputs.tf                     # Cluster endpoint, kubeconfig path
│
├── manifests/
│   ├── namespace.yaml                 # workshop namespace
│   ├── vllm-deployment.yaml           # vLLM inference server:
│   │                                  # - image: vllm/vllm-openai:latest
│   │                                  # - replicas: 5 (one per GPU node)
│   │                                  # - args: --model Qwen/Qwen3-8B
│   │                                  #         --gpu-memory-utilization 0.9
│   │                                  #         --max-model-len 8192
│   │                                  # - GPU resource request: nvidia.com/gpu: 1
│   │                                  # - readiness probe: GET /health
│   ├── vllm-service.yaml              # K8s Service: vllm:8000
│   │                                  # OpenAI-compatible API at /v1/chat/completions
│   ├── workspace-pods.yaml            # 80x code-server pods (generated by script)
│   │                                  # Each pod:
│   │                                  #   - Unique password via K8s Secret
│   │                                  #   - securityContext: nonRoot, no escalation
│   │                                  #   - resources: 1GB RAM, 500m CPU limit
│   │                                  #   - env: VLLM_HOST=http://vllm:8000/v1
│   ├── workspace-services.yaml        # ClusterIP service per pod
│   ├── ingress.yaml                   # Path-based routing: /s01/ → pod-01, etc.
│   │                                  # TLS via NodeBalancer
│   └── pod-security.yaml             # Pod Security Standards
│
├── scripts/
│   ├── provision.sh                   # terraform apply + kubectl apply manifests
│   ├── teardown.sh                    # kubectl delete ns workshop + terraform destroy
│   ├── generate-pods.sh               # Generates workspace-pods.yaml + workspace-services.yaml
│   │                                  # for N students. Creates K8s Secrets with random passwords.
│   │                                  # Outputs access-cards.csv: student_num, url, password
│   ├── health-check.sh               # Validates: vLLM pods healthy, model loaded,
│   │                                  # sample workspace pod, nba-stats-mcp works
│   ├── pre-warm.sh                    # Send test request to each vLLM pod to confirm
│   │                                  # model is loaded and responding
│   └── print-access-cards.sh          # Formats access-cards.csv for printing (HTML table)
│
├── images/
│   └── workspace/
│       └── Dockerfile                 # Student workspace image:
│                                      # FROM codercom/code-server:latest
│                                      # + Python 3.11 + venv + deps
│                                      # + workshop repo pre-cloned to /home/coder/workshop
│                                      # + VS Code Python extension pre-installed
│                                      # + make verify passes at build time
│                                      # + USER coder (non-root)
│
└── docs/
    ├── runbook.md                     # Day-of checklist for Du'An
    ├── troubleshooting.md             # Common issues + fixes
    └── security.md                    # Security model documentation
```

### Repo 3: `nba-discord-agent` (Already Exists)

No changes needed. This is the reference implementation students explore after. The workshop `reference/README.md` links here with a concept map.

---

## Workshop Flow: Minute by Minute

### What Students See When They Connect

```
┌─────────────────────────────────────────────────────────────┐
│  VS Code (browser)                                          │
│                                                             │
│  ┌─── EXPLORER ──┐  ┌─── EDITOR ─────────────────────────┐ │
│  │               │  │                                     │ │
│  │ docs/         │  │  01_first_agent.py                  │ │
│  │  00-setup.md  │  │                                     │ │
│  │  01-your-f... │  │  # =================== ...          │ │
│  │  02-tool-u... │  │  # SECTION 1: Your First Agent      │ │
│  │  ...          │  │  # =================== ...          │ │
│  │ workshop/     │  │  #                                  │ │
│  │  00_verify.py │  │  # What you'll learn:               │ │
│  │  01_first_... │  │  # - How to create a Strands agent  │ │
│  │  02_add_to... │  │  # - What happens when you ask ...  │ │
│  │  03_add_me... │  │  #                                  │ │
│  │  04_heartb... │  │  from strands import Agent          │ │
│  │  solutions/   │  │  from src.config import get_model   │ │
│  │ src/          │  │  ...                                │ │
│  │ reference/    │  │                                     │ │
│  │ extend/       │  │                                     │ │
│  │               │  │                                     │ │
│  └───────────────┘  └─────────────────────────────────────┘ │
│                                                             │
│  ┌─── TERMINAL ─────────────────────────────────────────┐   │
│  │ ~/workshop $ python workshop/00_verify.py            │   │
│  │ ✅ LLM: connected (Qwen3-8B via vLLM)               │   │
│  │ ✅ MCP Server: connected (6 tools available)         │   │
│  │ ✅ Strands SDK: v0.1.x                               │   │
│  │ ✅ You're ready!                                     │   │
│  │ ~/workshop $                                         │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

The workspace opens with `01_first_agent.py` in the editor and a terminal at the bottom. Instructions are in the code comments. Students read the code, run it in the terminal, modify it, re-run.

---

### Pre-Workshop (T-5 min)

- Students seated, QR code on screen with URL: `workshop.akamai-devrel.dev`
- Each student has a printed card with their unique URL + password
- QR code on the card links to their workspace (`workshop.akamai-devrel.dev/s42`)
- Workspace pod is already running (pre-created). Enter password, VS Code loads in seconds.
- `01_first_agent.py` is open in the editor when they land

---

### 0:00 - 0:03 | Opening

> "By the end of this hour, you'll understand the three patterns behind every production AI agent — and you'll have built one on a GPU sitting in an Akamai data center right now."

Quick context: who you are, what Akamai Cloud offers for AI workloads, what Strands Agents is. 30 seconds max on each.

---

### 0:03 - 0:08 | The Hook: Live Demo

Demo the full NBA Discord Agent live in a Discord server.

1. Ask it: "What happened in the Lakers game last night?" — agent calls tools, responds with stats
2. Follow up: "How did LeBron play?" — agent remembers the prior question, gives contextual answer
3. Show the heartbeat channel: "This message was posted autonomously at 7am — the agent decided a game recap was worth sharing"

> "Three things just happened: it used tools to get data, it remembered our conversation, and it acted on its own. Those are the three patterns. Let's build each one."

---

### 0:08 - 0:10 | Orient: "Here's Your Environment"

> "You should have VS Code open in your browser. You'll see the project files on the left, code in the middle, terminal at the bottom. Let's make sure everything works."

Students run in their terminal:

```
python workshop/00_verify.py
```

Output:
```
✅ LLM: connected (Qwen3-8B via vLLM)
✅ MCP Server: connected (6 tools available)
✅ Strands SDK: v0.1.x
✅ You're ready!
```

If anyone gets a red X, DA partner helps them. Everyone else moves on.

> "You have an LLM running on a GPU in the cloud via vLLM, an NBA stats MCP server, and the Strands agent framework. Let's build."

---

### 0:10 - 0:20 | Section 1: Your First Agent (10 min)

> "Open `workshop/01_first_agent.py` — it should already be open. Read through the code, then run it."

**`01_first_agent.py`** contains:

```python
# ============================================================
# SECTION 1: Your First Agent
# ============================================================
#
# What you'll learn:
# - How to create a Strands agent with a system prompt
# - What happens when the agent only has its training data
# - Why agents need tools to be useful
#
# Run this: python workshop/01_first_agent.py
# ============================================================

from strands import Agent
from src.config import get_model

# Create the simplest possible agent — an LLM with a role
agent = Agent(
    model=get_model(),
    system_prompt="You are a helpful NBA analyst."
)

# Ask something it knows from training data
print("\n--- Question 1: From training data ---")
response = agent("Who won the NBA championship last year?")
print(response)

# Now ask something it CAN'T know — today's scores
print("\n--- Question 2: Real-time data ---")
response = agent("What's the score of today's Warriors game?")
print(response)

# ============================================================
# TRY THIS: Change the question above. Ask about a game today.
# Notice: the agent guesses or says "I don't know."
# It has no way to look up real data. It needs tools.
#
# When you're ready: python workshop/02_add_tools.py
# ============================================================
```

Du'An explains while students run: "This is the simplest agent. It can only answer from training data. Ask about today's games — it can't help. It has no hands."

---

### 0:20 - 0:35 | Section 2: Give It Tools (15 min)

> "Now run `python workshop/02_add_tools.py` and watch the terminal output carefully."

**`02_add_tools.py`** contains:

```python
# ============================================================
# SECTION 2: Give It Tools — An Agent That Acts
# ============================================================
#
# What you'll learn:
# - Tools come from MCP servers — external processes that
#   provide capabilities to your agent
# - The LLM DECIDES which tool to call based on your question
# - Hooks let you SEE the agent's tool calls in real time
#
# Run this: python workshop/02_add_tools.py
# ============================================================

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient
from src.hooks import ToolDisplayHook
from src.config import get_model

# Connect to the NBA stats MCP server (runs as a local subprocess)
mcp_client = MCPClient(
    lambda: stdio_client(StdioServerParameters(command="nba-stats-mcp")),
)

# The MCP server provides tools — you don't define them in Python.
# The context manager starts the server, discovers tools, and cleans up.
with mcp_client:
    tools = mcp_client.list_tools_sync()

    print(f"--- MCP server provided {len(tools)} tools ---")
    for tool in tools:
        print(f"  - {tool.tool_name}: {tool.tool_description[:60]}...")
    print()

    # Create an agent WITH tools and a display hook
    agent = Agent(
        model=get_model(),
        tools=tools,
        hooks=[ToolDisplayHook()]    # <-- This prints tool calls to your terminal
    )

    # Ask the same question that failed in Section 1
    print("--- Ask about today's games (watch the tool calls!) ---")
    response = agent("What's the score of today's Warriors game?")

    # ============================================================
    # TRY THIS: Run these one at a time. Watch which tool the agent picks:
    #
    #   response = agent("How did Jokic play tonight?")
    #   response = agent("Who's leading the Western Conference?")
    #   response = agent("Tell me a joke about basketball")
    #
    # Notice: the agent calls different tools for different questions.
    # For the joke, it calls NO tools. It reasons about what it needs.
    #
    # The tools came from the MCP server, not from your code.
    # Run: nba-stats-mcp --help to see what the server provides.
    #
    # When you're ready: python workshop/03_add_memory.py
    # ============================================================
```

**What students see in the terminal** (colored output from hooks):

```
--- MCP server provided 6 tools ---
  - get_scoreboard: Get today's NBA scoreboard with live scores and gam...
  - get_box_score: Get detailed box score for a specific game includi...
  - get_standings: Get current NBA standings by conference and divisi...
  - find_game: Find a specific game by team name and date...
  - get_schedule: Get the NBA schedule for a given date range...
  - get_player_stats: Get player statistics for a specific game...

--- Ask about today's games (watch the tool calls!) ---
🤔 Agent thinking...
🔧 Tool Call: get_scoreboard(date="20260505")
📊 Result: GSW 108 - LAL 102 (Final) | DEN 95 - MIN 88 (Q3 8:42)...
🤔 Agent reasoning with tool results...

The Warriors beat the Lakers 108-102 today. Steph Curry led
the way with 28 points...
```

> "You didn't write any routing logic. The model read the tool descriptions from the MCP server, chose the right tool, formatted the parameters, and used the result. That's tool-use. The tools came from `nba-stats-mcp` — a separate process your agent discovered at runtime."

Students spend 3 minutes experimenting with different questions in the terminal, watching which tools the agent chooses.

---

### 0:35 - 0:45 | Section 3: Give It Memory (10 min)

> "Run `python workshop/03_add_memory.py`. This one shows a before-and-after."

**`03_add_memory.py`** contains:

```python
# ============================================================
# SECTION 3: Give It Memory — An Agent That Remembers
# ============================================================
#
# What you'll learn:
# - Why agents without memory can't handle follow-up questions
# - How SlidingWindowConversationManager gives the agent context
# - The trade-off: memory uses tokens, so we set a window size
#
# Run this: python workshop/03_add_memory.py
# ============================================================

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from src.tools.mcp import build_mcp_client
from src.hooks import ToolDisplayHook
from src.config import get_model

mcp_client = build_mcp_client()

with mcp_client:
    tools = mcp_client.list_tools_sync()

    # --- WITHOUT memory ---
    print("=" * 50)
    print("WITHOUT MEMORY")
    print("=" * 50)

    agent_no_memory = Agent(
        model=get_model(),
        tools=tools,
        hooks=[ToolDisplayHook()]
    )

    agent_no_memory("What's the score of today's Nuggets game?")
    print()
    agent_no_memory("How did their center play?")
    # ❌ Agent doesn't know who "their" refers to

    # --- WITH memory ---
    print("\n" + "=" * 50)
    print("WITH MEMORY")
    print("=" * 50)

    agent_with_memory = Agent(
        model=get_model(),
        tools=tools,
        hooks=[ToolDisplayHook()],
        conversation_manager=SlidingWindowConversationManager(window_size=10)
    )

    agent_with_memory("What's the score of today's Nuggets game?")
    print()
    agent_with_memory("How did their center play?")
    # ✅ Agent knows "their" = Nuggets, "center" = Jokic

    # ============================================================
    # TRY THIS: Keep talking to agent_with_memory:
    #
    #   agent_with_memory("What about the other team's best player?")
    #   agent_with_memory("Compare their stats")
    #
    # The agent tracks the full conversation. The window_size=10
    # means it remembers the last 10 messages. Try changing it to 2
    # and see what happens with a long conversation.
    #
    # When you're ready: python workshop/04_heartbeat.py
    # ============================================================
```

> "One constructor argument — `conversation_manager`. The agent now maintains a sliding window. It knows 'their' means Denver because it remembers your last question. This is how the production agent handles multi-turn conversations in Discord."

---

### 0:45 - 0:55 | Section 4: The Heartbeat (10 min)

> "This is the one you need to edit. Open `workshop/04_heartbeat.py`, read it, change the criteria, then run it."

**`04_heartbeat.py`** contains:

```python
# ============================================================
# SECTION 4: The Heartbeat — An Agent That Thinks Alone
# ============================================================
#
# What you'll learn:
# - How an agent can act WITHOUT being asked
# - Natural language as control flow — you write criteria in
#   English, the agent interprets them and decides
# - The production NBA agent runs this every 60 seconds
#
# EDIT the heartbeat_criteria below, then run:
#   python workshop/04_heartbeat.py
# ============================================================

from src.heartbeat import get_current_context, run_heartbeat
from src.tools.mcp import build_mcp_client
from src.hooks import ToolDisplayHook
from src.config import get_model

# See what the agent knows about the current NBA landscape
context = get_current_context()
print("--- Current Context ---")
print(f"Time: {context['current_time']}")
print(f"Live games: {len(context['live_games'])}")
print(f"Recently finished: {len(context['recently_finished'])}")
print()

# ============================================================
# ✏️  EDIT THIS — Add your own criteria below
# ============================================================
heartbeat_criteria = """
You are monitoring NBA games. Review the current context and decide
if any action is worth taking. You should post when:

- A game just went Final and hasn't been recapped yet
- A player had a standout performance (35+ points, triple-double, etc.)

If nothing meets the criteria, respond with "No action needed" and
explain why briefly.
"""
# ============================================================
# IDEAS — try adding criteria like:
# - "A game is within 3 points in the 4th quarter"
# - "A rookie scored 20+ points"
# - "An overtime game just finished"
# - "A team broke a losing streak"
# ============================================================

mcp_client = build_mcp_client()

with mcp_client:
    tools = mcp_client.list_tools_sync()

    print("--- Triggering Heartbeat ---")
    result = run_heartbeat(
        model=get_model(),
        tools=tools,
        criteria=heartbeat_criteria,
        context=context,
        hooks=[ToolDisplayHook()]
    )

print("\n--- Agent Decision ---")
print(result)

# ============================================================
# TRY THIS: Change the criteria above and re-run.
# Make it pickier → agent says "No action needed"
# Make it broader → agent finds something to post about
#
# This is the core loop of the NBA Discord agent. In production,
# this runs every 60 seconds. The agent watches games all day
# and posts when its judgment says to. Not a cron job — reasoning.
# ============================================================
```

**What students see in terminal:**

```
--- Current Context ---
Time: 2026-05-05T19:45:00
Live games: 2
Recently finished: 3

--- Triggering Heartbeat ---
🤔 Agent evaluating context...
🔧 Tool Call: get_box_score(game_id="0022500892")
📊 Result: DEN 112 - MIN 108 (Final) | Jokic: 41 PTS, 15 REB, 9 AST...
🤔 Agent reasoning about criteria...

--- Agent Decision ---
✅ ACTION: Post game recap
"The Nuggets edged out the Timberwolves 112-108 in a thriller.
Nikola Jokic was dominant with 41 points, 15 rebounds, and
9 assists, falling one assist short of a triple-double..."
```

> "You wrote the criteria in English. The agent decided what was worth posting. Natural language as control flow. Modify the criteria, re-run, watch the agent reason differently."

Students spend remaining time editing criteria and re-running.

---

### 0:55 - 1:00 | The Send-off

> "In 45 minutes, you built the three patterns behind every production AI agent:
> 1. **Tools** — the agent decides which functions to call and when
> 2. **Memory** — the agent maintains context across interactions
> 3. **Autonomous reasoning** — the agent evaluates conditions and acts without being asked
>
> The NBA Discord agent I showed at the start? Same three patterns, fully built out."

**On screen:**

- Workshop repo link + QR code (they already have it)
- NBA Discord Agent reference repo link + QR code
- `extend/` folder: take-home challenges for going further
- `reference/README.md`: maps what you learned to production code
- Contact info / community for questions

---

## All Moving Pieces: Owner + Timeline

### T-45 Days: Planning

| Task | Owner | Notes |
|------|-------|-------|
| Confirm GPU region capacity (4x RTX 4000 Ada in one region) | Du'An → Akamai GPU team | Non-negotiable. If unavailable, pivot region. Need answer in 1 week. |
| Confirm LKE supports GPU node pools in target region | Du'An → Akamai K8s team | They will know immediately |
| Create `ai-agents-workshop` repo (public) | Du'An | Scaffold only — README + folder structure |
| Create `ai-agents-workshop-infra` repo (private) | Du'An + K8s team | They own this after initial design |
| Book venue at Stanford, confirm A/V (projector, WiFi for 80) | Du'An | WiFi capacity for 80 is critical — confirm with venue |

### T-30 Days: Build

| Task | Owner | Notes |
|------|-------|-------|
| Write workshop scripts (`01` through `04` + `00_verify`) | Du'An | THE deliverable. These scripts ARE the workshop. |
| Write `src/` agent code (agent.py, tools, hooks, heartbeat, config) | Du'An | Keep it minimal. < 200 lines total. |
| Write `workshop/solutions/` completed scripts | Du'An | Safety net for stuck students |
| Write `extend/` take-home challenges | Du'An | 4 markdown files, brief, actionable |
| Write `reference/README.md` concept map | Du'An | Maps workshop concepts → nba-discord-agent code |
| Write presentation slides (10 min) | Du'An | Hook demo + three-pattern framework |
| Configure `.vscode/settings.json` | Du'An | Python path, terminal defaults, so VS Code just works |
| Build Terraform for LKE cluster | K8s team | CPU pool + GPU pool |
| Write `generate-pods.sh` script | K8s team | Generates pod manifests, secrets, services, access cards |
| Build vLLM K8s deployment | K8s team | vllm-deployment.yaml + vllm-service.yaml, Qwen3-8B, 5 replicas |
| Build student workspace Docker image | Du'An + K8s team | code-server + Python 3.11 + deps + repo + VS Code extensions |
| Verify nba-stats-mcp works in workspace image | Du'An | `pip install nba-stats-mcp` in Dockerfile, test stdio connection |

### T-14 Days: Integrate

| Task | Owner | Notes |
|------|-------|-------|
| Deploy full stack to LKE (first time) | K8s team + Du'An | Terraform apply + Helm install + kubectl apply |
| Run all workshop scripts end-to-end in a workspace pod | Du'An | Every script must work. Fix any issues. |
| Load test: simulate 20 concurrent users | K8s team | Hit vLLM with 20 parallel requests, measure latency |
| Write `scripts/health-check.sh` | K8s team | Validates vLLM pods, workspace pods, nba-stats-mcp in workspace |
| Run `generate-pods.sh` for 80 students | K8s team | Creates pod manifests + access-cards.csv |
| Write `docs/runbook.md` | Du'An | Day-of checklist |

### T-7 Days: Dry Run

| Task | Owner | Notes |
|------|-------|-------|
| Dry run with DA partner + 3-5 volunteers | Du'An + DA | Run the full 1-hour workshop. Time it. |
| Identify friction points, fix scripts | Du'An | Where did people get stuck? Where did timing break? |
| Tear down and re-provision from scratch | K8s team | Validate provisioning is repeatable |

### T-1 Day: Pre-Provision

| Task | Owner | Notes |
|------|-------|-------|
| Run `provision.sh` | K8s team or Du'An | Full cluster up |
| Run `generate-pods.sh -n 80` | Du'An | Generates manifests + access-cards.csv |
| `kubectl apply -f manifests/` | Du'An | All 80 workspace pods + services created |
| Run `pre-warm.sh` | Du'An | Sends test request to each vLLM pod, confirms model loaded |
| Run `health-check.sh` | Du'An | Validates all pods, vLLM, nba-stats-mcp |
| Run `print-access-cards.sh` | Du'An | Generates printable HTML from access-cards.csv |
| Print access cards | Du'An | One card per student: URL + password |

### Day Of: Workshop

| Time | Action | Owner |
|------|--------|-------|
| T-60 min | Run `health-check.sh` again | Du'An |
| T-30 min | Open a test workspace (s01), run `00_verify.py` through `04_heartbeat.py` | Du'An |
| T-15 min | DA partner at back of room, ready with printed access cards | DA |
| T-5 min | QR code on projector, students arriving and connecting | Du'An |
| 0:00 | Workshop begins | Du'An presents |
| 0:00-1:00 | DA partner circulates, helps stuck students, points to `solutions/` | DA |
| T+30 min | Run `teardown.sh` | Du'An or K8s team |

---

## Fallback Plan

| Scenario | Response |
|----------|----------|
| **GPU instances unavailable in region** | Deploy vLLM on Dedicated CPU instances (slower but works — Qwen3 4B on CPU at ~10 tok/s, acceptable for workshop) |
| **LKE GPU node pool not supported** | Deploy vLLM on standalone GPU Linodes outside the cluster. Workspace pods call vLLM via external IP + NodeBalancer. Same UX for students. |
| **Workspace slow to load** | Pre-create all 80 workspaces the night before (don't rely on on-demand creation). Workspaces are already running when students arrive. |
| **Student completely stuck** | DA points them to `workshop/solutions/` folder. They run the completed script, see the output, catch up to the group. |
| **Inference latency spike (everyone runs same script simultaneously)** | Du'An paces: "Run this now" → waits 10 seconds → "See the output?" vLLM's continuous batching handles bursts well, but staggering by 5 seconds helps. |
| **Total infra failure** | Du'An switches to live demo from own laptop. Students follow along reading the scripts conceptually. Always have a demo-from-laptop escape hatch. |

---

## What Students Walk Away With

1. **The repo** — `ai-agents-workshop` is theirs. Their modified scripts. The source code. The take-home challenges.
2. **The reference** — `nba-discord-agent` shows what production looks like. The concept map connects workshop learning to real code.
3. **The mental model** — Agents = tools + memory + reasoning loops. Not just "LLM with a system prompt."
4. **A running example they can extend** — Take-home challenges are concrete: deploy to Discord, add persistence, build multi-agent, swap the domain.
5. **GPU experience** — They ran inference on a real GPU. They saw the speed. They know local LLMs are viable.

---

## Sources

- [GPU Linodes - Akamai TechDocs](https://techdocs.akamai.com/cloud-computing/docs/gpu-compute-instances)
- [RTX 4000 Ada GPUs on Linode](https://www.linode.com/blog/compute/new-gpus-nvidia-rtx-4000-ada-generation/)
- [LKE AI Inference with App Platform](https://www.linode.com/docs/guides/deploy-llm-for-ai-inferencing-on-apl/)
- [code-server on Kubernetes](https://deepwiki.com/coder/code-server/5.3-kubernetes-and-helm-deployment)
- [K8s Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [vLLM Documentation](https://docs.vllm.ai/)
- [vLLM Kubernetes Deployment Guide](https://dasroot.net/posts/2026/02/deploying-vllm-scale-kubernetes/)
- [Strands Agents SDK](https://strandsagents.com/docs/)
- [Strands Workshop (Zero to Hero)](https://github.com/LondheShubham153/strands-agents-workshop)
