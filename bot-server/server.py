"""
Claude Botminton Agent - Telegram Bot Server

Listens for Telegram messages and routes them to a Claude CLI session.
Each Telegram chat gets its own persistent Claude session (resumed via session_id).
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SESSIONS_FILE = "/app/data/sessions.json"
CLAUDE_WORKDIR = "/app"
MAX_TG_MESSAGE_LENGTH = 4096


def load_sessions() -> dict:
    path = Path(SESSIONS_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_sessions(sessions: dict) -> None:
    path = Path(SESSIONS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sessions, indent=2))


def run_claude(message: str, session_id: str | None) -> tuple[str, str | None]:
    """
    Invoke the Claude CLI in print mode.
    Returns (response_text, new_session_id).
    """
    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd.extend(["--resume", session_id])
    cmd.append(message)

    logger.info("Running claude: %s (session=%s)", " ".join(cmd[:5] + ["..."]), session_id)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=CLAUDE_WORKDIR,
        env={**os.environ, "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY},
        timeout=300,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("Claude CLI error (exit %d): %s", result.returncode, stderr)
        raise RuntimeError(f"Claude CLI failed: {stderr or 'unknown error'}")

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("Claude CLI returned empty output")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Claude may occasionally output plain text — return as-is
        logger.warning("Claude output was not JSON, returning raw text")
        return stdout, session_id

    response_text = data.get("result", "")
    new_session_id = data.get("session_id") or session_id

    return response_text, new_session_id


def split_message(text: str) -> list[str]:
    """Split a long message into Telegram-safe chunks."""
    if len(text) <= MAX_TG_MESSAGE_LENGTH:
        return [text]

    parts = []
    while len(text) > MAX_TG_MESSAGE_LENGTH:
        # Try to split at a newline boundary
        split_at = text.rfind("\n", 0, MAX_TG_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = MAX_TG_MESSAGE_LENGTH
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = str(update.message.chat_id)
    user_text = update.message.text.strip()

    logger.info("Message from chat_id=%s: %s", chat_id, user_text[:80])

    await update.message.chat.send_action(ChatAction.TYPING)

    sessions = load_sessions()
    session_id = sessions.get(chat_id)

    try:
        loop = asyncio.get_running_loop()
        response_text, new_session_id = await loop.run_in_executor(
            None, run_claude, user_text, session_id
        )
    except RuntimeError as e:
        logger.error("Claude error: %s", e)
        await update.message.reply_text(
            f"⚠️ Claude encountered an error:\n{e}\n\nPlease try again."
        )
        return
    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timed out")
        await update.message.reply_text(
            "⏱️ Request timed out (Claude took too long). Please try again."
        )
        return

    if new_session_id:
        sessions[chat_id] = new_session_id
        save_sessions(sessions)

    for chunk in split_message(response_text):
        await update.message.reply_text(chunk, parse_mode="Markdown")


def main() -> None:
    logger.info("Starting Claude Botminton Agent...")
    Path("/app/data").mkdir(parents=True, exist_ok=True)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is polling for messages...")
    # run_polling manages its own event loop — do NOT wrap in asyncio.run()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
