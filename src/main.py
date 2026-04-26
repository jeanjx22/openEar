"""Main entry point for openEar.

Initializes and runs the single async Python process:
1. Load configuration
2. Set up logging (console + rotating file)
3. Initialize database
4. Create service instances
5. Build Telegram bot application with handlers
6. Start APScheduler
7. Run bot polling loop

All components share the same async event loop.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram.ext import Application

from src.bot.handlers import BotHandlers
from src.config import load_settings
from src.db.database import init_db
from src.services.backup_service import BackupService
from src.services.email_service import EmailService
from src.services.health_service import HealthService
from src.services.llm_service import LLMService
from src.services.note_service import NoteService
from src.services.reminder_service import ReminderService

logger = logging.getLogger("openear")


def setup_logging(log_level: str, log_dir: str) -> None:
    """Configure logging with console and rotating file handlers."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # Rotating file handler: 10 MB per file, keep 5 files (50 MB total)
    file_handler = RotatingFileHandler(
        log_path / "openear.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)


def main() -> None:
    """Main entry point."""
    # 1. Load configuration
    settings = load_settings()

    # 2. Set up logging
    setup_logging(settings.log_level, settings.log_dir)
    logger.info("Starting openEar...")

    # Validate required settings
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)
    llm_key = settings.cohere_api_key if settings.llm_provider == "cohere" else settings.groq_api_key
    if not llm_key:
        logger.error("LLM API key is not set for provider '%s'. Exiting.", settings.llm_provider)
        sys.exit(1)
    if not settings.telegram_allowed_user_ids:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS is empty -- all users will be rejected")

    # 3. Initialize database
    init_db(settings.db_path)

    # 4. Create service instances (in-memory, no external connections yet)
    llm_service = LLMService(settings)
    email_service = EmailService(settings, llm_service)
    reminder_service = ReminderService(settings)
    note_service = NoteService()
    health_service = HealthService(settings)
    backup_service = BackupService(settings)

    # 5. Build Telegram bot application
    from src.scheduler.jobs import SchedulerJobs

    scheduler_jobs = None

    async def post_init(application):
        nonlocal scheduler_jobs
        application.bot_data["allowed_user_ids"] = settings.telegram_allowed_user_ids
        # Seed active_chat_ids with individual user IDs so scheduled
        # messages are delivered even before any user sends a message.
        # Group chat IDs are added dynamically by handlers._track_chat
        # as users interact with the bot in group chats.
        application.bot_data["active_chat_ids"] = set(
            settings.telegram_allowed_user_ids
        )

        scheduler_jobs = SchedulerJobs(
            settings=settings,
            app=application,
            llm_service=llm_service,
            email_service=email_service,
            reminder_service=reminder_service,
            health_service=health_service,
            backup_service=backup_service,
        )
        scheduler_jobs.setup()
        scheduler_jobs.start()
        logger.info("openEar is running.")

    async def post_shutdown(application):
        if scheduler_jobs:
            scheduler_jobs.shutdown()
        logger.info("openEar stopped.")

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Register handlers
    bot_handlers = BotHandlers(
        settings=settings,
        llm_service=llm_service,
        email_service=email_service,
        reminder_service=reminder_service,
        note_service=note_service,
        health_service=health_service,
    )
    for handler in bot_handlers.get_handlers():
        app.add_handler(handler)

    async def error_handler(update, context):
        logger.error("Unhandled exception: %s", context.error, exc_info=context.error)
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Oops, something went wrong! Please try again 🐰"
                )
            except Exception:
                pass

    app.add_error_handler(error_handler)

    # 6. Run bot polling loop (blocks until stopped)
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
