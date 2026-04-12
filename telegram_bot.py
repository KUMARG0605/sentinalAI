"""
telegram_bot.py — SentinelAI Telegram Integration

Allows users to send prompts to SentinelAI through a Telegram bot.
The bot receives messages, passes them to Orchestrator.run(), and
replies with the answer.

Setup:
  1. pip install python-telegram-bot==20.*
  2. Create a bot via @BotFather on Telegram → get BOT_TOKEN
  3. Set TELEGRAM_BOT_TOKEN in your .env file
  4. Optionally set TELEGRAM_ALLOWED_IDS (comma-separated chat IDs) to restrict access
  5. Run: python telegram_bot.py

Usage after running:
  - Open your bot in Telegram
  - Send any message — it is forwarded to Sentinel as a prompt
  - The bot replies with Sentinel's answer
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

# ── ensure project root on path ───────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
dotenv.load_dotenv(PROJECT_ROOT / ".env")

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction

from app.src.orchestrator import Orchestrator

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("sentinel.telegram")

# ── config ────────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set.\n"
        "Add it to your .env file:  TELEGRAM_BOT_TOKEN=your_token_here"
    )

# Optional: restrict access to specific Telegram user/chat IDs
# Set in .env as:  TELEGRAM_ALLOWED_IDS=123456789,987654321
_raw_ids = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
ALLOWED_IDS: set[int] = (
    {int(i.strip()) for i in _raw_ids.split(",") if i.strip()}
    if _raw_ids.strip()
    else set()
)

# ── shared orchestrator (thread-safe via its own internal locks) ──────────────
_orchestrator: Orchestrator | None = None
_orch_lock = threading.Lock()


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        with _orch_lock:
            if _orchestrator is None:
                logger.info("Initialising Sentinel Orchestrator…")
                _orchestrator = Orchestrator()
                logger.info("Orchestrator ready.")
    return _orchestrator


# ── access guard ──────────────────────────────────────────────────────────────
def _is_allowed(update: Update) -> bool:
    """Return True if the sender is allowed to use the bot."""
    if not ALLOWED_IDS:
        return True  # open to everyone if no whitelist configured
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    return (chat_id in ALLOWED_IDS) or (user_id in ALLOWED_IDS)


# ── handlers ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start."""
    if not _is_allowed(update):
        await update.message.reply_text("⛔ You are not authorised to use this bot.")
        return
    await update.message.reply_text(
        "👋 Hi! I'm *Sentinel*, your AI desktop assistant.\n\n"
        "Just send me any message and I'll get right on it.\n\n"
        "Commands:\n"
        "  /start — show this message\n"
        "  /help  — show usage tips",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help."""
    if not _is_allowed(update):
        await update.message.reply_text("⛔ You are not authorised to use this bot.")
        return
    await update.message.reply_text(
        "💡 *How to use Sentinel via Telegram*\n\n"
        "Just type your prompt naturally, for example:\n"
        "  • `Search for flights from Chennai to Delhi`\n"
        "  • `Open Notepad and write a shopping list`\n"
        "  • `What's the weather in Tirupati today?`\n\n"
        "Sentinel will process your request and reply with the result.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main handler: receive text prompt → run Orchestrator → reply with answer.
    Runs the blocking orchestrator call in a thread pool so the bot stays responsive.
    """
    if not update.message or not update.message.text:
        return

    if not _is_allowed(update):
        await update.message.reply_text("⛔ You are not authorised to use this bot.")
        return

    prompt = update.message.text.strip()
    if not prompt:
        return

    logger.info("Telegram prompt from %s: %s", update.effective_user.id, prompt[:80])

    # Show "typing…" indicator while processing
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    # Run the blocking orchestrator in a thread so the event loop isn't blocked
    loop = asyncio.get_event_loop()
    try:
        result: dict = await loop.run_in_executor(
            None,
            lambda: get_orchestrator().run(prompt),
        )
        answer: str = result.get("answer") or "✅ Done (no text answer returned)."
    except Exception as exc:
        logger.exception("Orchestrator error for prompt: %s", prompt)
        answer = f"❌ Error while processing your request:\n`{exc}`"

    # Telegram has a 4096-char message limit; split if necessary
    MAX_LEN = 4000
    if len(answer) <= MAX_LEN:
        await update.message.reply_text(answer, parse_mode="Markdown")
    else:
        # Send in chunks
        chunks = [answer[i : i + MAX_LEN] for i in range(0, len(answer), MAX_LEN)]
        for i, chunk in enumerate(chunks):
            prefix = f"*[{i+1}/{len(chunks)}]*\n" if len(chunks) > 1 else ""
            await update.message.reply_text(prefix + chunk, parse_mode="Markdown")


# ── entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    logger.info("Starting Sentinel Telegram bot…")

    # Warm up the orchestrator in a background thread so the first message
    # is not slow (optional — comment out if startup delay is acceptable)
    threading.Thread(target=get_orchestrator, daemon=True).start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot is polling for updates… (Ctrl-C to stop)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()