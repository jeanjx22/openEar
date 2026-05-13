"""Telegram user ID authentication filter.

Applied as the outermost filter in every handler group. Messages
from unauthorized users are silently dropped -- no response, no LLM
call, no logging of message content (only the rejected user ID).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def auth_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the user is authorized.

    Returns True if allowed, False if rejected.
    Unauthorized messages are silently dropped.
    """
    allowed_ids: set[int] = context.bot_data.get("allowed_user_ids", set())

    if not update.effective_user:
        return False

    user_id = update.effective_user.id
    if user_id not in allowed_ids:
        logger.warning("Rejected message from user_id=%s", user_id)
        return False

    return True
