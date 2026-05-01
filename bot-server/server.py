"""
Claude Botminton Agent - Telegram Bot Server

Listens for Telegram messages and routes them to a Claude CLI session.
Each Telegram chat gets its own persistent Claude session (resumed via session_id).
"""

import asyncio
import json  # still used for sessions.json
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


def run_claude(message: str, has_prior_session: bool) -> str:
    """
    Invoke the Claude CLI in print mode via stdin.
    Uses --continue for follow-up messages to resume the last session.
    Returns the plain-text response.
    """
    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
    ]
    if has_prior_session:
        cmd.append("--continue")

    logger.info("Running claude (continue=%s): %s", has_prior_session, message[:80])
    logger.info("Full command: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        input=message,           # pass message via stdin
        capture_output=True,
        text=True,
        cwd=CLAUDE_WORKDIR,
        env={**os.environ, "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY},
        timeout=300,
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    logger.info("Claude exit code: %d", result.returncode)
    logger.info("Claude stdout (%d chars): %s", len(stdout), stdout[:300])
    if stderr:
        logger.info("Claude stderr (%d chars): %s", len(stderr), stderr[:500])

    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI failed: {stderr or stdout or 'unknown error'}")

    # If stdout is empty but stderr has content, the CLI may write to stderr
    if not stdout and stderr:
        logger.warning("stdout empty, using stderr as response")
        return stderr

    if not stdout:
        raise RuntimeError(f"Claude CLI returned empty output. stderr: {stderr}")

    return stdout


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
    has_prior_session = sessions.get(chat_id, False)

    try:
        loop = asyncio.get_running_loop()
        response_text = await loop.run_in_executor(
            None, run_claude, user_text, has_prior_session
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

    sessions[chat_id] = True
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
