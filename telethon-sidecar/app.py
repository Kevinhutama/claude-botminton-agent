"""
Telethon Sidecar - FastAPI service that uses your personal Telegram account
to read messages from groups that don't accept bots.

Endpoints:
  GET  /health              - Health check
  GET  /messages             - Read recent messages from a group
  POST /send                 - Send a message to a group as you
  POST /dm                   - Send a direct/private message to a user
  GET  /dm/messages          - Read recent DM messages with a specific user
  GET  /groups               - List your joined groups (helper to find group IDs)
  GET  /auth/status          - Check if authenticated
  POST /auth/send-code       - Start auth flow (sends code to your phone)
  POST /auth/verify-code     - Complete auth with the code you received
  POST /auth/verify-2fa      - Complete auth with 2FA password (if enabled)
  GET  /calendar/auth-url    - Get Google OAuth URL to authorize calendar access
  GET  /calendar/callback    - OAuth callback (paste code here)
  POST /calendar/add         - Add an event to Google Calendar
"""

import os
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.messages import GetHistoryRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration from environment ---
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
PHONE = os.environ.get("TELEGRAM_PHONE", "")
SESSION_DIR = "/app/session"
SESSION_FILE = os.path.join(SESSION_DIR, "telethon.session")

# --- Google Calendar config ---
GCAL_CREDS_DIR = "/app/gcal"
GCAL_CREDENTIALS_FILE = os.path.join(GCAL_CREDS_DIR, "credentials.json")
GCAL_TOKEN_FILE = os.path.join(GCAL_CREDS_DIR, "token.json")
GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

client: Optional[TelegramClient] = None
phone_code_hash: Optional[str] = None

# Queue of incoming DMs waiting to be picked up by the bot-server
incoming_dm_queue: list[dict] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop the Telethon client with the FastAPI app."""
    global client
    if API_ID == 0 or not API_HASH:
        logger.warning("TELEGRAM_API_ID / TELEGRAM_API_HASH not set. Auth endpoints available.")
        client = None
    else:
        client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info(f"Authenticated as: {me.first_name} ({me.phone})")
            _register_event_handlers(client)
        else:
            logger.info("Client connected but not authenticated. Use /auth endpoints.")
    yield
    if client and client.is_connected():
        await client.disconnect()


def _register_event_handlers(tg_client: TelegramClient):
    """Register Telethon event handlers for incoming DMs."""
    from telethon import events

    @tg_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_incoming_dm(event):
        """Capture incoming private messages into the queue."""
        sender = await event.get_sender()
        sender_name = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        if last:
            sender_name += f" {last}"
        username = getattr(sender, "username", None)

        entry = {
            "id": event.message.id,
            "date": event.message.date.isoformat() if event.message.date else None,
            "sender": sender_name,
            "sender_id": event.sender_id,
            "sender_username": username,
            "text": event.message.text or "",
        }
        incoming_dm_queue.append(entry)
        logger.info(f"Queued incoming DM from {sender_name} (@{username}): {entry['text'][:80]}")


app = FastAPI(
    title="Telethon Sidecar",
    description="Reads Telegram group messages using your personal account",
    lifespan=lifespan,
)


# ──────────────────────────────────────────────
# Health & Auth
# ──────────────────────────────────────────────

@app.get("/health")
async def health():
    connected = client is not None and client.is_connected()
    authorized = connected and await client.is_user_authorized() if connected else False
    return {"status": "ok", "connected": connected, "authorized": authorized}


@app.get("/auth/status")
async def auth_status():
    if client is None:
        raise HTTPException(status_code=503, detail="Client not initialized. Check API_ID and API_HASH.")
    connected = client.is_connected()
    authorized = await client.is_user_authorized() if connected else False
    return {"connected": connected, "authorized": authorized}


class SendCodeRequest(BaseModel):
    phone: Optional[str] = None


@app.post("/auth/send-code")
async def auth_send_code(req: SendCodeRequest):
    """Send a login code to your phone. First step of authentication."""
    global phone_code_hash
    if client is None:
        raise HTTPException(status_code=503, detail="Client not initialized.")

    phone = req.phone or PHONE
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required (env or body).")

    result = await client.send_code_request(phone)
    phone_code_hash = result.phone_code_hash
    return {"message": f"Code sent to {phone}. Use /auth/verify-code to complete."}


class VerifyCodeRequest(BaseModel):
    phone: Optional[str] = None
    code: str


@app.post("/auth/verify-code")
async def auth_verify_code(req: VerifyCodeRequest):
    """Verify the code sent to your phone. Second step of authentication."""
    global phone_code_hash
    if client is None:
        raise HTTPException(status_code=503, detail="Client not initialized.")

    phone = req.phone or PHONE
    try:
        await client.sign_in(phone=phone, code=req.code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        return {"message": f"Authenticated as {me.first_name}", "authorized": True}
    except SessionPasswordNeededError:
        return {
            "message": "2FA is enabled. Use /auth/verify-2fa with your password.",
            "needs_2fa": True,
            "authorized": False,
        }


class Verify2FARequest(BaseModel):
    password: str


@app.post("/auth/verify-2fa")
async def auth_verify_2fa(req: Verify2FARequest):
    """Complete authentication with 2FA password."""
    if client is None:
        raise HTTPException(status_code=503, detail="Client not initialized.")

    await client.sign_in(password=req.password)
    me = await client.get_me()
    return {"message": f"Authenticated as {me.first_name}", "authorized": True}


# ──────────────────────────────────────────────
# Telegram Group Operations
# ──────────────────────────────────────────────

def _require_auth():
    if client is None or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client not connected.")


@app.get("/groups")
async def list_groups():
    """List all groups/supergroups you are a member of. Useful to find group IDs."""
    _require_auth()
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Not authenticated. Use /auth endpoints first.")

    from telethon.tl.types import Channel, Chat

    dialogs = await client.get_dialogs()
    groups = []
    for d in dialogs:
        if isinstance(d.entity, (Channel, Chat)):
            if hasattr(d.entity, "megagroup") and d.entity.megagroup:
                group_type = "supergroup"
            elif isinstance(d.entity, Chat):
                group_type = "group"
            elif isinstance(d.entity, Channel):
                group_type = "channel"
            else:
                group_type = "unknown"
            groups.append({
                "id": d.entity.id,
                "title": d.title,
                "type": group_type,
                "username": getattr(d.entity, "username", None),
            })
    return {"groups": groups}


@app.get("/messages")
async def get_messages(
    group: str = Query(..., description="Group username (without @) or numeric ID"),
    limit: int = Query(50, ge=1, le=200, description="Number of recent messages to fetch"),
    search: Optional[str] = Query(None, description="Optional text to filter messages"),
):
    """
    Read recent messages from a Telegram group using your personal account.
    Returns messages with id, date, sender, text, and a deep link.
    """
    _require_auth()
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        if group.lstrip("-").isdigit():
            entity = await client.get_entity(int(group))
        else:
            entity = await client.get_entity(group)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Group not found: {e}")

    messages = []
    async for msg in client.iter_messages(entity, limit=limit, search=search):
        sender_name = ""
        if msg.sender:
            sender_name = getattr(msg.sender, "first_name", "") or ""
            last = getattr(msg.sender, "last_name", "") or ""
            if last:
                sender_name += f" {last}"
            username = getattr(msg.sender, "username", None)
        else:
            username = None

        # Build deep link
        group_username = getattr(entity, "username", None)
        if group_username:
            link = f"https://t.me/{group_username}/{msg.id}"
        else:
            link = f"https://t.me/c/{entity.id}/{msg.id}"

        messages.append({
            "id": msg.id,
            "date": msg.date.isoformat() if msg.date else None,
            "sender": sender_name,
            "sender_id": msg.sender_id,
            "sender_username": username,
            "text": msg.text or "",
            "link": link,
        })

    return {"group": group, "count": len(messages), "messages": messages}


class SendMessageRequest(BaseModel):
    group: str
    text: str
    reply_to: Optional[int] = None


@app.post("/send")
async def send_message(req: SendMessageRequest):
    """Send a message to a group as your personal account."""
    _require_auth()
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        if req.group.lstrip("-").isdigit():
            entity = await client.get_entity(int(req.group))
        else:
            entity = await client.get_entity(req.group)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Group not found: {e}")

    try:
        result = await client.send_message(
            entity,
            req.text,
            reply_to=req.reply_to,
        )
        logger.info(f"Message sent to group {req.group}, message_id: {result.id}")
        return {"message_id": result.id, "sent": True}
    except Exception as e:
        logger.error(f"Failed to send message to group {req.group}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send message: {e}")


# ──────────────────────────────────────────────
# Direct Messages (DMs)
# ──────────────────────────────────────────────

class DMRequest(BaseModel):
    user: str  # username (without @) or numeric user ID
    text: str


@app.post("/dm")
async def send_dm(req: DMRequest):
    """Send a direct/private message to a Telegram user as your personal account."""
    _require_auth()
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        if req.user.lstrip("-").isdigit():
            entity = await client.get_entity(int(req.user))
        else:
            entity = await client.get_entity(req.user)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"User not found: {e}")

    try:
        result = await client.send_message(entity, req.text)
        logger.info(f"DM sent to {req.user}, message_id: {result.id}")
        return {"message_id": result.id, "sent": True, "to_user": req.user}
    except Exception as e:
        logger.error(f"Failed to send DM to {req.user}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send message: {e}")


@app.get("/dm/incoming")
async def get_incoming_dms(clear: bool = True):
    """
    Return all queued incoming DMs (messages sent TO you by others).
    Clears the queue by default so messages are only returned once.
    """
    global incoming_dm_queue
    messages = list(incoming_dm_queue)
    if clear:
        incoming_dm_queue.clear()
    return {"count": len(messages), "messages": messages}


@app.get("/dm/messages")
async def get_dm_messages(
    user: str = Query(..., description="Username (without @) or numeric user ID"),
    limit: int = Query(10, ge=1, le=100, description="Number of recent DMs to fetch"),
):
    """
    Read recent direct messages between you and a specific user.
    Returns messages in reverse chronological order (newest first).
    """
    _require_auth()
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        if user.lstrip("-").isdigit():
            entity = await client.get_entity(int(user))
        else:
            entity = await client.get_entity(user)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"User not found: {e}")

    me = await client.get_me()
    messages = []
    async for msg in client.iter_messages(entity, limit=limit):
        is_from_me = msg.sender_id == me.id
        sender_name = ""
        if msg.sender:
            sender_name = getattr(msg.sender, "first_name", "") or ""
            last = getattr(msg.sender, "last_name", "") or ""
            if last:
                sender_name += f" {last}"

        messages.append({
            "id": msg.id,
            "date": msg.date.isoformat() if msg.date else None,
            "from_me": is_from_me,
            "sender": sender_name,
            "text": msg.text or "",
        })

    return {"user": user, "count": len(messages), "messages": messages}


# ──────────────────────────────────────────────
# Google Calendar
# ──────────────────────────────────────────────

def _get_gcal_service():
    """Build and return an authenticated Google Calendar service."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not os.path.exists(GCAL_CREDENTIALS_FILE):
        raise HTTPException(
            status_code=503,
            detail="Google credentials not set up. See /calendar/setup-instructions."
        )

    creds = None
    if os.path.exists(GCAL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GCAL_TOKEN_FILE, GCAL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            Path(GCAL_CREDS_DIR).mkdir(parents=True, exist_ok=True)
            with open(GCAL_TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        else:
            raise HTTPException(
                status_code=401,
                detail="Google Calendar not authorized. Visit GET /calendar/auth-url to authorize."
            )

    return build("calendar", "v3", credentials=creds)


@app.get("/calendar/setup-instructions")
async def calendar_setup_instructions():
    """Returns step-by-step instructions for setting up Google Calendar access."""
    return {
        "instructions": [
            "1. Go to https://console.cloud.google.com/",
            "2. Create a new project (or select existing)",
            "3. Enable 'Google Calendar API' under APIs & Services > Library",
            "4. Go to APIs & Services > Credentials > Create Credentials > OAuth 2.0 Client ID",
            "5. Choose 'Desktop app' as application type",
            "6. Download the JSON file and save it as 'credentials.json'",
            "7. Copy it into the container: docker cp credentials.json telethon-sidecar:/app/gcal/credentials.json",
            "8. Visit GET /calendar/auth-url to get your authorization URL",
            "9. Open the URL in your browser, authorize, copy the code",
            "10. Call GET /calendar/callback?code=YOUR_CODE_HERE",
        ]
    }


@app.get("/calendar/auth-url")
async def calendar_auth_url():
    """Get the Google OAuth authorization URL. Visit this URL in your browser."""
    from google_auth_oauthlib.flow import Flow

    if not os.path.exists(GCAL_CREDENTIALS_FILE):
        raise HTTPException(
            status_code=503,
            detail="credentials.json not found. See /calendar/setup-instructions."
        )

    flow = Flow.from_client_secrets_file(
        GCAL_CREDENTIALS_FILE,
        scopes=GCAL_SCOPES,
        redirect_uri="http://localhost:8081/calendar/callback",
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return {
        "auth_url": auth_url,
        "instructions": "Open this URL in your browser, authorize access. You will be redirected automatically."
    }


@app.get("/calendar/callback")
async def calendar_callback(
    code: str = Query(None, description="Authorization code from Google OAuth"),
    error: str = Query(None, description="Error from Google OAuth"),
):
    """Complete OAuth flow — Google redirects here automatically after authorization."""
    from google_auth_oauthlib.flow import Flow

    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="No code provided.")

    if not os.path.exists(GCAL_CREDENTIALS_FILE):
        raise HTTPException(status_code=503, detail="credentials.json not found.")

    flow = Flow.from_client_secrets_file(
        GCAL_CREDENTIALS_FILE,
        scopes=GCAL_SCOPES,
        redirect_uri="http://localhost:8081/calendar/callback",
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    Path(GCAL_CREDS_DIR).mkdir(parents=True, exist_ok=True)
    with open(GCAL_TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    return {"message": "✅ Google Calendar authorized successfully! You can now ask the bot to add games to your calendar."}


class CalendarEventRequest(BaseModel):
    summary: str                          # Event title e.g. "🏸 Badminton @ Clementi Sports Hall"
    location: str                         # Venue
    description: str                      # Host, level, price, group link
    start_datetime: str                   # ISO format: "2026-02-22T19:00:00"
    end_datetime: str                     # ISO format: "2026-02-22T21:00:00"
    timezone: str = "Asia/Singapore"
    calendar_id: str = "primary"
    attendees: Optional[list[str]] = None  # List of emails


@app.post("/calendar/add")
async def calendar_add_event(req: CalendarEventRequest):
    """Add a badminton game event to Google Calendar."""
    service = _get_gcal_service()

    event = {
        "summary": req.summary,
        "location": req.location,
        "description": req.description,
        "start": {
            "dateTime": req.start_datetime,
            "timeZone": req.timezone,
        },
        "end": {
            "dateTime": req.end_datetime,
            "timeZone": req.timezone,
        },
    }

    if req.attendees:
        event["attendees"] = [{"email": email} for email in req.attendees]

    created = service.events().insert(calendarId=req.calendar_id, body=event).execute()
    return {
        "success": True,
        "event_id": created.get("id"),
        "event_link": created.get("htmlLink"),
        "summary": created.get("summary"),
    }
