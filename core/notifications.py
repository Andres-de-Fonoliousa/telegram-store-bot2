import asyncio
import logging
from telegram import Bot
from telegram.error import TelegramError
from core.database import SessionLocal
from core.models import User

logger = logging.getLogger(__name__)


async def send_notification(bot: Bot, telegram_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    """
    Send a message to a user by Telegram ID.
    Returns True if successful, False otherwise.
    """
    try:
        await bot.send_message(chat_id=telegram_id, text=text, parse_mode=parse_mode)
        return True
    except TelegramError as e:
        logger.warning(f"Failed to send notification to {telegram_id}: {e}")
        return False


def notify_user_sync(bot_token: str, telegram_id: int, text: str, parse_mode: str = "Markdown"):
    """
    Synchronous wrapper for sending notifications from non‑async contexts.
    Use only if you cannot run async code.
    """
    try:
        bot = Bot(token=bot_token)
        # Run async function in a new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(send_notification(bot, telegram_id, text, parse_mode))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Sync notification failed: {e}")
        return False


def get_user_telegram_id(user_id: int) -> int | None:
    """Fetch user's Telegram ID from database."""
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        return user.telegram_id if user else None
    finally:
        db.close()