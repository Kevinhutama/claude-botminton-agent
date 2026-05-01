# Claude Botminton Agent

A personal badminton session finder bot powered by **Claude CLI** + **Telethon**.

You message your Telegram bot → a Claude CLI session starts → Claude reads the SG Badminton
Community group via your personal Telegram account → Claude finds slots, DMs hosts, and adds
games to your Google Calendar.

## Architecture

```
┌─────────────────────────────── Docker (clawnet) ───────────────────────────────┐
│                                                                                  │
│  ┌──────────────────────────────┐     ┌───────────────────────────────────┐     │
│  │   bot-server                 │     │   telethon-sidecar                │     │
│  │   (Python + Claude CLI)      │     │   (Your Personal TG Account)      │     │
│  │                              │     │                                   │     │
│  │  - Telegram Bot polling      │────▶│  - MTProto / personal login       │     │
│  │  - Claude CLI subprocess     │     │  - Reads any group you're in      │     │
│  │  - Session management        │◀────│  - REST API on :8081              │     │
│  │  - CLAUDE.md agent context   │     │  - Sends DMs as you               │     │
│  └──────────────────────────────┘     └───────────────────────────────────┘     │
│           ▲                                                                      │
│           │ Bot API (polling)                                                    │
└───────────┼──────────────────────────────────────────────────────────────────────┘
            │
       ┌────┴─────┐
       │    You    │
       │ (Telegram)│
       └──────────┘
```

**How it works:**
1. You message your Telegram bot
2. `bot-server` receives the message and runs:
   ```
   claude --print --output-format json [--resume <session_id>] --message "<your message>"
   ```
3. Claude reads `CLAUDE.md` for instructions, calls `http://telethon-sidecar:8081` via curl (built-in Bash tool)
4. Claude returns a structured JSON response
5. `bot-server` sends the reply back to your Telegram and saves the session ID for continuity
6. Your next message resumes the same Claude conversation

---

## Prerequisites

| Tool | How to get it |
|------|--------------|
| **Colima** (or Docker Desktop) | `brew install colima` |
| **Git** | `brew install git` |
| **curl** | Pre-installed on macOS |

---

## Step 1: Get Your Credentials

### 1A. Telegram Bot Token (from @BotFather)

1. Open Telegram → search **@BotFather** → `/newbot`
2. Follow the prompts, save the bot token (e.g., `7123456789:AAHxx...`)

### 1B. Telegram Personal API Credentials

1. Go to **https://my.telegram.org** → log in
2. Click **"API development tools"** → Create application
3. Save your `App api_id` and `App api_hash`

### 1C. Anthropic API Key

1. Go to **https://console.anthropic.com** → API Keys → Create Key
2. Save the key (starts with `sk-ant-...`)

> Note: Requires a pay-as-you-go Anthropic account. Claude Pro/Max subscription does NOT include API access.

---

## Step 2: Configure Environment

```bash
cp .env.example .env
```

Fill in all values in `.env`:

```bash
TELEGRAM_BOT_TOKEN=7123456789:AAHxxxxxxxxxx
TELEGRAM_API_ID=2XXXXXXX
TELEGRAM_API_HASH=a1b2c3d4e5f6g7h8i9j0k1l2m3n4
TELEGRAM_PHONE=+628123456789
ANTHROPIC_API_KEY=sk-ant-xxxxx
```

---

## Step 3: Start Colima

```bash
dev shell
colima start --cpu 4 --memory 4 --disk 60
```

All subsequent commands must also be run inside `dev shell`.

---

## Step 4: Start the GrabGPT Proxy (Grab Engineers Only)

The bot uses Claude via GrabGPT. You must run the local proxy on port 9898 **before** starting Docker:

```bash
PORT=9898 CHUNK_EAGER=0 CHUNK_MAX_WAIT_MS=500 \
  UPSTREAM_ORIGIN="https://public-api.grabgpt.managed.catwalk-k8s.stg-myteksi.com" \
  npx -y git+https://oauth2:<token>@gitlab.myteksi.net/design/cc-grabgpt-proxy.git
```

Keep this running in a separate terminal. The Docker container connects to it via `host.docker.internal:9898`.

---

## Step 5: Build and Start Services

```bash
cd ~/Workspace/botminton/claude-botminton-agent
docker compose up -d --build
```

First build takes ~5 minutes (installs Node.js, Claude CLI, Python deps).

Verify both containers are running:
```bash
docker compose ps
docker logs claude-botminton-bot -f
```

---

## Step 6: Authenticate Your Personal Telegram Account

The Telethon sidecar needs a one-time login to read the badminton group. Just run:

```bash
./auth.sh
```

The script will:
1. Send an OTP to your Telegram account
2. Prompt you to enter the code
3. Handle 2FA if enabled
4. Confirm authentication with a health check

The session is persisted in a Docker volume — no need to re-authenticate after restarts.

---

## Step 7: Connect Google Calendar (Optional)

Lets Claude add confirmed badminton games directly to your Google Calendar.

### 6A. Create OAuth Credentials

1. Go to **https://console.cloud.google.com** → create/select project
2. Enable **Google Calendar API** (APIs & Services → Library)
3. OAuth consent screen: External, add your Gmail as test user
4. Credentials → Create → OAuth 2.0 Client ID → Desktop app → Download JSON

### 6B. Install Credentials

```bash
cp ~/Downloads/client_secret_*.json ./gcal/credentials.json
```

### 6C. Authorize

```bash
# Get the auth URL
curl -s http://localhost:8081/calendar/auth-url | python3 -m json.tool
```

Open the `auth_url` in your browser → authorize → Google redirects back to
`http://localhost:8081/calendar/callback` → you'll see a success message.

---

## Step 8: Start Using It!

Message your Telegram bot:

- *"Find available badminton slots this Saturday"*
- *"Any HB-LI games this weekend near Clementi?"*
- *"I want to join game #2"*
- *"Did the host reply?"*
- *"Add it to my calendar with Gaby"*

---

## After Laptop Restart

Everything is persisted in Docker volumes. Just run:

```bash
dev shell
colima start
cd ~/Workspace/botminton/claude-botminton-agent
docker compose up -d
```

---

## Useful Commands

### Logs

```bash
# Tail all logs (most useful for debugging)
docker logs claude-botminton-bot -f

# Tail just the telethon sidecar
docker logs claude-botminton-telethon -f

# Via docker compose (equivalent)
docker compose logs -f
docker compose logs -f claude-botminton-bot
docker compose logs -f claude-botminton-telethon
```

### Service Management

```bash
# Check running containers
docker compose ps

# Restart after CLAUDE.md changes (no rebuild needed)
docker compose restart claude-botminton-bot

# Rebuild after server.py / Dockerfile changes
docker compose build bot-server && docker compose up -d bot-server

# Force recreate with latest env vars
docker compose up -d --force-recreate bot-server
```

### Claude CLI (inside container)

```bash
# Enter the container
docker exec -it -u botuser claude-botminton-bot /bin/bash

# Test Claude CLI directly
claude --print --dangerously-skip-permissions --no-session-persistence "Say hello"

# Check Claude version
claude --version
```

### Reset Claude Conversation

```bash
# Clear conversation history for all chats (start fresh)
docker exec claude-botminton-bot python3 -c "
import pathlib
pathlib.Path('/app/data/sessions.json').write_text('{}')
print('Sessions cleared')
"
```

### Sidecar API

```bash
curl -s http://localhost:8081/health | python3 -m json.tool
curl -s "http://localhost:8081/messages?group=sgbadmintontelecom&limit=5" | python3 -m json.tool
curl -s http://localhost:8081/groups | python3 -m json.tool
```

---

## Project Structure

```
claude-botminton-agent/
├── CLAUDE.md                   # Agent instructions (Claude reads this)
├── README.md                   # This file
├── docker-compose.yml          # Orchestrates both services
├── .env.example                # Template for secrets
├── .env                        # Your secrets (git-ignored)
├── .gitignore
│
├── gcal/                       # Google Calendar OAuth files (git-ignored)
│   ├── credentials.json        # OAuth client credentials (you provide)
│   └── token.json              # OAuth access token (auto-generated)
│
├── bot-server/                 # Telegram bot + Claude CLI orchestrator
│   ├── Dockerfile              # Python 3.12 + Node.js + Claude CLI
│   ├── requirements.txt        # python-telegram-bot
│   └── server.py               # Main server: polling + subprocess + sessions
│
└── telethon-sidecar/           # Personal TG account + Google Calendar service
    ├── Dockerfile
    ├── requirements.txt
    └── app.py                  # FastAPI + Telethon + Google Calendar API
```

---

## Troubleshooting

### Bot doesn't respond
```bash
docker compose logs -f claude-botminton-bot
```
- Verify `TELEGRAM_BOT_TOKEN` is correct in `.env`
- Make sure `bot-server` container is `Up` in `docker compose ps`

### "Claude CLI failed" in logs
- Verify `ANTHROPIC_API_KEY` is set and valid
- Check if Claude CLI is installed: `docker compose exec claude-botminton-bot claude --version`

### Telethon not authorized
```bash
curl -s http://localhost:8081/health
# If "authorized": false → redo Step 5
```

### Google Calendar not working
```bash
ls -la gcal/   # Should have credentials.json and token.json
curl -s http://localhost:8081/calendar/auth-url  # Re-authorize if needed
```

### Start a fresh Claude conversation
The session is tied to your Telegram chat_id. To reset:
```bash
docker compose exec claude-botminton-bot python3 -c "
import json, pathlib
p = pathlib.Path('/app/data/sessions.json')
p.write_text('{}')
print('Sessions cleared')
"
```

### Reset everything
```bash
docker compose down -v
docker compose up -d --build
# Then redo Step 5 (Telethon auth) and Step 6 (Google Calendar)
```
