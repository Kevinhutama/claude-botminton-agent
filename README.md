# Claude Botminton Agent

A personal badminton session finder bot powered by **Claude CLI** + **Telethon**.

You message your Telegram bot → Claude finds available slots from the SG Badminton Community group → Claude DMs hosts on your behalf → you get notified automatically when they reply.

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
│  │  - Conversation history      │◀────│  - REST API on :8081              │     │
│  │  - Incoming DM monitor       │     │  - Sends DMs as you               │     │
│  └──────────────────────────────┘     └───────────────────────────────────┘     │
│           ▲                                    ▲                                 │
│           │ Bot API (polling)                  │ Bedrock proxy                   │
└───────────┼────────────────────────────────────┼─────────────────────────────────┘
            │                          host.docker.internal:9898
       ┌────┴─────┐                    (cc-grabgpt-proxy on Mac)
       │    You    │
       │ (Telegram)│
       └──────────┘
```

**How it works:**
1. You message your Telegram bot
2. `bot-server` runs `claude --print --dangerously-skip-permissions --no-session-persistence "<prompt>"` with full conversation history injected
3. Claude reads `CLAUDE.md` for instructions, uses its Bash tool to `curl` the Telethon sidecar
4. Claude finds slots, DMs hosts, checks replies, adds to calendar
5. The bot also monitors for incoming DMs every 30s — when a host replies, Claude analyzes it and notifies you automatically (and asks for PayNow if not provided)

---

## Prerequisites

| Tool | How to get it |
|------|--------------|
| **Colima** | `brew install colima` |
| **Node.js** | `brew install node` (for npx) |
| **Git** | `brew install git` |

---

## Setup (First Time Only)

### Step 1: Get Your Credentials

**1A. Telegram Bot Token**
1. Open Telegram → search **@BotFather** → `/newbot`
2. Save the bot token (e.g., `7123456789:AAHxx...`)

**1B. Telegram Personal API Credentials**
1. Go to **https://my.telegram.org** → log in
2. Click **"API development tools"** → Create application
3. Save your `App api_id` and `App api_hash`

**1C. GrabGPT API Key & Proxy Token**
- `ANTHROPIC_API_KEY`: your GrabGPT UUID token (from `~/.zshrc`)
- `GRABGPT_PROXY_TOKEN`: your GitLab OAuth token for `cc-grabgpt-proxy`

---

### Step 2: Configure Environment

```bash
cp .env.example .env
```

Fill in all values:

```bash
TELEGRAM_BOT_TOKEN=7123456789:AAHxxxxxxxxxx
TELEGRAM_API_ID=2XXXXXXX
TELEGRAM_API_HASH=a1b2c3d4e5f6g7h8i9j0k1l2m3n4
TELEGRAM_PHONE=+628123456789
ANTHROPIC_API_KEY=your-grabgpt-uuid-token
GRABGPT_PROXY_TOKEN=your-gitlab-oauth-token
```

---

### Step 3: Build Docker Images

```bash
cd ~/Workspace/botminton/claude-botminton-agent
docker compose build
```

First build takes ~5 minutes (installs Node.js, Claude CLI, Python deps).

---

### Step 4: Start the Bot

```bash
./botminton-start
```

This single command handles everything:
1. Checks Colima — starts it if not running (skips if already running)
2. Starts the GrabGPT proxy on port 9898 (skips if already running)
3. Runs `docker compose up -d`

---

### Step 5: Authenticate Your Personal Telegram Account

One-time login so the sidecar can read the badminton group and send DMs as you:

```bash
./botminton-auth
```

The script sends an OTP to your phone, prompts for the code, handles 2FA, and confirms auth.
The session is persisted in a Docker volume — **no need to re-authenticate after restarts**.

---

### Step 6: Connect Google Calendar (Optional)

Lets Claude add confirmed games directly to your Google Calendar.

**6A.** Go to **https://console.cloud.google.com** → enable **Google Calendar API** → create an OAuth 2.0 Client ID (Desktop app) → download the JSON

**6B.** Copy credentials:
```bash
cp ~/Downloads/client_secret_*.json ./gcal/credentials.json
```

**6C.** Authorize:
```bash
curl -s http://localhost:8081/calendar/auth-url | python3 -m json.tool
```
Open the `auth_url` in your browser → authorize → done.

---

### Step 7: Start Using It

Message your Telegram bot:

- *"Find HB games this Saturday"*
- *"Any LI-MI games this weekend near Clementi?"*
- *"I want to join game #2"*
- *"Did the host reply?"*
- *"Add it to my calendar with Gaby"*

Host replies are forwarded to you automatically within 30 seconds — no need to ask.

---

## Day-to-Day Usage

### Start

```bash
cd ~/Workspace/botminton/claude-botminton-agent
./botminton-start
```

### Stop

```bash
./botminton-stop
```

Stops Docker services and the GrabGPT proxy. Colima is left running (other programs may use it).

### Re-authenticate Telethon (if session expires)

```bash
./botminton-auth
```

---

## Useful Commands

### Logs

```bash
docker logs claude-botminton-bot -f         # Bot server (main)
docker logs claude-botminton-telethon -f    # Telethon sidecar
```

### Service Management

```bash
docker compose ps                            # Check container status
docker compose restart claude-botminton-bot  # Restart after CLAUDE.md changes
docker compose build bot-server && docker compose up -d bot-server  # Rebuild after code changes
```

### Claude CLI (inside container)

```bash
docker exec -it -u botuser claude-botminton-bot /bin/bash
claude --print --dangerously-skip-permissions --no-session-persistence "Say hello"
```

### Reset Claude Conversation

```bash
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
├── CLAUDE.md                   # Agent instructions (Claude reads this on every call)
├── README.md
├── docker-compose.yml
├── .env.example                # Template for secrets
├── .env                        # Your secrets (git-ignored)
├── botminton-start             # Start everything (Colima + proxy + Docker)
├── botminton-stop              # Stop Docker services + proxy
├── botminton-auth              # Authenticate Telethon personal account
├── auth.sh                     # Called by botminton-auth
│
├── gcal/                       # Google Calendar OAuth files (git-ignored)
│   ├── credentials.json
│   └── token.json
│
├── bot-server/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py               # Telegram polling + Claude subprocess + DM monitor
│   └── entrypoint.sh           # Fixes volume permissions, drops to botuser
│
└── telethon-sidecar/
    ├── Dockerfile
    ├── requirements.txt
    └── app.py                  # FastAPI + Telethon + Google Calendar + DM event handler
```

---

## Troubleshooting

### Bot doesn't respond
```bash
docker logs claude-botminton-bot -f
```
- Check `TELEGRAM_BOT_TOKEN` in `.env`
- Verify GrabGPT proxy is running: `lsof -iTCP:9898 -sTCP:LISTEN`

### Claude fails / empty output
- Check proxy is reachable: `docker exec -u botuser claude-botminton-bot curl -s http://host.docker.internal:9898`
- Test Claude inside container: `docker exec -it -u botuser claude-botminton-bot /bin/bash` then `claude --print --dangerously-skip-permissions "hi"`

### Telethon not authorized
```bash
curl -s http://localhost:8081/health
# "authorized": false → run ./botminton-auth
```

### Google Calendar not working
```bash
ls -la gcal/     # Should have credentials.json and token.json
curl -s http://localhost:8081/calendar/auth-url | python3 -m json.tool
```

### Reset everything
```bash
./botminton-stop
docker compose down -v    # removes volumes too (will need to re-auth Telethon)
docker compose build
./botminton-start
./botminton-auth
```
