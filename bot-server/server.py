"""
Claude Botminton Agent - Telegram Bot Server

Listens for Telegram messages and routes them to a Claude CLI session.
Conversation history is maintained per chat_id and injected into each prompt.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import aiohttp
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
MAX_HISTORY_TURNS = 10  # number of past exchanges to include in context
SIDECAR_URL = "http://telethon-sidecar:8081"
DM_POLL_INTERVAL = 30  # seconds between incoming DM checks


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


def build_prompt(message: str, history: list) -> str:
    """Build a full prompt including conversation history."""
    parts = []
    if history:
        parts.append("Previous conversation:")
        for turn in history[-MAX_HISTORY_TURNS:]:
            parts.append(f"User: {turn['user']}")
            parts.append(f"Assistant: {turn['assistant']}")
        parts.append("")
    parts.append(f"User: {message}")
    return "\n".join(parts)


def run_claude(prompt: str) -> str:
    """
    Invoke the Claude CLI using the same pattern as the working bolt app.
    Prompt is passed as a positional argument with HOME set explicitly.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError("claude CLI not found in PATH")

    cmd = [
        claude_path,
        "--print",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--verbose",
        prompt,
    ]

    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "HOME": str(Path.home()),
    }

    logger.info("Running claude (prompt length: %d chars)", len(prompt))
    logger.info("Claude path: %s | CWD: %s", claude_path, CLAUDE_WORKDIR)

    process = subprocess.Popen(
        cmd,
        cwd=CLAUDE_WORKDIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    stdout_lines = []
    if process.stdout:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            stdout_lines.append(line)

    process.wait(timeout=300)

    stderr_output = ""
    if process.stderr:
        stderr_output = process.stderr.read()
        if stderr_output:
            logger.info("Claude stderr: %s", stderr_output[:500])

    logger.info("Claude exit code: %d", process.returncode)

    if process.returncode != 0:
        raise RuntimeError(f"Claude CLI failed: {stderr_output or 'unknown error'}")

    response = "".join(stdout_lines).strip()
    logger.info("Claude response length: %d chars", len(response))

    if not response:
        raise RuntimeError(f"Claude CLI returned empty output. stderr: {stderr_output}")

    return response


def split_message(text: str) -> list[str]:
    """Split a long message into Telegram-safe chunks."""
    if len(text) <= MAX_TG_MESSAGE_LENGTH:
        return [text]
    parts = []
    while len(text) > MAX_TG_MESSAGE_LENGTH:
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
    history = sessions.get(chat_id, [])
    prompt = build_prompt(user_text, history)

    try:
        loop = asyncio.get_running_loop()
        response_text = await loop.run_in_executor(None, run_claude, prompt)
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

    # Persist conversation turn
    history.append({"user": user_text, "assistant": response_text})
    sessions[chat_id] = history[-MAX_HISTORY_TURNS:]
    save_sessions(sessions)

    for chunk in split_message(response_text):
        await update.message.reply_text(chunk, parse_mode="Markdown")


def get_primary_chat_id() -> str | None:
    """Return the most recently active chat_id to send DM notifications to."""
    sessions = load_sessions()
    if not sessions:
        return None
    # Return the last key (most recently written)
    return list(sessions.keys())[-1]


async def poll_incoming_dms(app: Application) -> None:
    """Background task: check for new incoming DMs every DM_POLL_INTERVAL seconds."""
    logger.info("Starting incoming DM polling every %ds", DM_POLL_INTERVAL)
    await asyncio.sleep(10)  # wait for sidecar to be ready

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{SIDECAR_URL}/dm/incoming", timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        messages = data.get("messages", [])
                        if messages:
                            chat_id = get_primary_chat_id()
                            if chat_id:
                                for msg in messages:
                                    sender = msg.get("sender_username") or msg.get("sender") or "Unknown"
                                    text = msg.get("text", "")
                                    notification = (
                                        f"📩 *Reply from @{sender}:*\n\n{text}"
                                        if msg.get("sender_username")
                                        else f"📩 *Reply from {sender}:*\n\n{text}"
                                    )
                                    await app.bot.send_message(
                                        chat_id=chat_id,
                                        text=notification,
                                        parse_mode="Markdown",
                                    )
                                    logger.info("Forwarded DM reply from %s to chat %s", sender, chat_id)
        except Exception as e:
            logger.debug("DM poll error (will retry): %s", e)

        await asyncio.sleep(DM_POLL_INTERVAL)


async def post_init(app: Application) -> None:
    """Called after PTB initialises — start background tasks here."""
    asyncio.create_task(poll_incoming_dms(app))


def main() -> None:
    logger.info("Starting Claude Botminton Agent...")
    Path("/app/data").mkdir(parents=True, exist_ok=True)

    claude_path = shutil.which("claude")
    logger.info("Claude CLI path: %s", claude_path)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is polling for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
