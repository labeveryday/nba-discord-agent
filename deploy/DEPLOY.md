# Deploy to Linode

Run the NBA Discord Agent on a hardened Linode with Anthropic as the model backend. Infrastructure is managed with Terraform using the same hardening pattern from [secure-linode](https://github.com/labeveryday/akamai-work-stuff/tree/main/terraform-examples/secure-linode), plus Docker pre-installed via cloud-init.

**Cost:** ~$12/mo (Nanode 2GB) — no GPU needed when using Anthropic.

---

## Prerequisites

1. **Terraform** — [Install guide](https://developer.hashicorp.com/terraform/install)
2. **A Linode API token** — [cloud.linode.com/profile/tokens](https://cloud.linode.com/profile/tokens) (Read/Write for Linodes and Firewalls)
3. **An SSH key pair** — `ssh-keygen -t ed25519` if you don't have one
4. **A Discord bot token** — [Discord Developer Portal](https://discord.com/developers/applications) (enable **Message Content Intent**)
5. **An Anthropic API key** — [console.anthropic.com](https://console.anthropic.com)

---

## Step 1: Configure Terraform

```bash
cd deploy
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
linode_token = "your-linode-api-token"
root_pass    = "a-strong-password"

authorized_keys = [
  "ssh-ed25519 AAAA... you@machine",
]

# Your current IP is always included automatically.
# Add extra IPs (e.g. work computer) — find yours at https://api.ipify.org
# allowed_ips = ["203.0.113.50/32"]
```

To get your public key:

```bash
cat ~/.ssh/id_ed25519.pub
```

## Step 2: Deploy the hardened Linode

```bash
terraform init
terraform plan     # Review what will be created
terraform apply    # Type "yes"
```

This creates:
- A **Nanode 2GB** ($12/mo) running Ubuntu 24.04
- A **Cloud Firewall** — SSH from your IP only, all other inbound dropped
- **Cloud-init hardening** on first boot:
  - Non-root `deploy` user (root locked entirely)
  - SSH: key-only, no passwords, no forwarding, max 3 auth tries
  - fail2ban (3 failures = 24hr ban)
  - UFW host firewall (mirrors cloud firewall — defense in depth)
  - Automatic security updates
  - **Docker CE + Compose plugin** pre-installed

Terraform prints the SSH command when done:

```
ssh_command = "ssh -p 22 deploy@<instance-ip>"
```

## Step 3: Wait for cloud-init (~3 minutes)

Cloud-init installs packages, hardens the OS, and installs Docker on first boot. Wait for it to finish before doing anything.

```bash
# SSH in using the command from terraform output
ssh deploy@<instance-ip>

# Check if cloud-init is done — should say "status: done"
cloud-init status

# If it says "running", wait a minute and check again
# You can watch it live with:
cloud-init status --wait
```

Verify Docker is ready:

```bash
docker --version
docker compose version
```

## Step 4: Clone the repo

```bash
git clone https://github.com/labeveryday/nba-discord-agent.git
cd nba-discord-agent
```

## Step 5: Create the `.env` file

```bash
cp env.example .env
nano .env
```

Set these values:

```bash
DISCORD_TOKEN=your-discord-bot-token
MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
# ANTHROPIC_MODEL defaults to claude-haiku-4-5-20251001 — fast, cheap, good for tool use.
# If responses feel too shallow, step up to claude-sonnet-4-20250514.
HEARTBEAT_CHANNEL_ID=your-channel-id
HEARTBEAT_ENABLED=true
```

Optional but recommended — get alerts when the bot starts or errors:

```bash
ALERTS_WEBHOOK_URL=https://discord.com/api/webhooks/your-webhook-url
```

Lock down the file:

```bash
chmod 600 .env
```

## Step 6: Build and start the bot

```bash
docker compose up -d --build
```

This builds the Docker image and starts the bot in the background.

## Step 7: Verify it's running

```bash
# Watch the logs
docker compose logs -f

# You should see:
#   Logged in as YourBot#1234
#   Heartbeat loop started
```

In Discord, type `$status`. The bot should respond with uptime and `anthropic:claude-haiku-4-5-20251001`.

Type `$help` to see all available commands.

---

## Day-to-day operations

### SSH back in

```bash
ssh deploy@<instance-ip>
cd nba-discord-agent
```

### View logs

```bash
docker compose logs -f --tail 100
```

### Restart the bot

```bash
docker compose restart
```

### Pull updates and redeploy

```bash
git pull
docker compose build && docker compose up -d --force-recreate
```

### Check what the heartbeat posted today

```bash
docker exec nba-discord-agent sqlite3 /app/data/agent.db \
    'select * from heartbeat_log where posted_at >= date("now");'
```

### Check resource usage

```bash
docker stats nba-discord-agent
```

### Reset heartbeat state (re-post today's recap/preview)

```bash
docker exec nba-discord-agent sqlite3 /app/data/agent.db \
    'delete from heartbeat_log where posted_at >= date("now");'
docker compose restart
```

See [OPERATIONS.md](../OPERATIONS.md) for the full operations guide.

---

## Tear down

```bash
# On the Linode — stop the bot
ssh deploy@<instance-ip>
cd nba-discord-agent
docker compose down

# On your local machine — destroy the Linode + firewall
cd deploy
terraform destroy   # Type "yes"
```

---

## Security summary

| Layer | Protection |
|---|---|
| **Cloud Firewall** | Network edge — SSH from your IP only, restricted outbound (DNS/NTP/HTTP/HTTPS) |
| **UFW** | Host firewall — mirrors cloud firewall (defense in depth) |
| **SSH** | Key-only, no root, no passwords, fail2ban (24hr ban after 3 failures) |
| **OS** | Automatic security updates, root account locked |
| **Container** | Non-root user, read-only filesystem, all capabilities dropped, no-new-privileges, 768MB limit |
| **Secrets** | `.env` mode 600, never in image or repo; `terraform.tfvars` gitignored |

The bot makes outbound-only connections to `discord.com`, `api.anthropic.com`, and NBA stats APIs. No inbound ports are exposed beyond SSH.

---

## File structure

```
deploy/
├── main.tf                  # Linode instance + Cloud Firewall
├── variables.tf             # Input variables
├── outputs.tf               # IP, SSH command, deploy steps
├── versions.tf              # Terraform + provider versions
├── cloud-init.yaml          # OS hardening + Docker install (runs on first boot)
├── terraform.tfvars.example # Template (copy to terraform.tfvars)
├── DEPLOY.md                # This file
└── .gitignore               # Keeps tfvars, state, .terraform/ out of git
```
