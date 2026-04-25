"""APScheduler job definitions for openEar.

Jobs:
- Email briefing: runs at configured check_times
- Reminder check: runs every minute to fire due reminders
- Heartbeat: runs daily at configured heartbeat_time
- Backup: runs daily at configured backup_time
- Snoozed reminders: runs every minute to wake expired snoozes
- Heartbeat file: writes /tmp/openear_heartbeat every 60s (C4)

C2: _reminder_check_job marks reminders as "notified" after sending.
C4: _heartbeat_file_job writes heartbeat file for Docker HEALTHCHECK.
C5: _send_to_all catches Forbidden errors with clear warning.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram.error import Forbidden
from telegram.ext import Application

from src.bot import formatters, keyboards
from src.config import Settings
from src.services.backup_service import BackupService
from src.services.email_service import EmailService, EmailServiceUnavailable
from src.services.health_service import HealthService
from src.services.llm_service import LLMService
from src.services.reminder_service import ReminderService

logger = logging.getLogger(__name__)

# C4: heartbeat file path for Docker HEALTHCHECK
HEARTBEAT_FILE = "/tmp/openear_heartbeat"


class SchedulerJobs:
    """Manages all scheduled jobs."""

    def __init__(
        self,
        settings: Settings,
        app: Application,
        llm_service: LLMService,
        email_service: EmailService,
        reminder_service: ReminderService,
        health_service: HealthService,
        backup_service: BackupService,
    ) -> None:
        self.settings = settings
        self.app = app
        self.llm = llm_service
        self.email = email_service
        self.reminders = reminder_service
        self.health = health_service
        self.backup = backup_service
        self.scheduler = AsyncIOScheduler()
        # Track which reminders have already received a post-event prompt
        # to avoid re-sending every minute
        self._post_prompted: set[int] = set()

        # The primary chat ID to send scheduled messages to
        # Uses the first allowed user ID
        self._chat_ids = list(settings.telegram_allowed_user_ids)

    async def _send_to_all(self, text: str, reply_markup=None) -> None:
        """Send a message to all allowed users.

        C5: Catches Forbidden errors (user hasn't sent /start to the bot)
        and logs a clear warning instead of silently failing.
        """
        for chat_id in self._chat_ids:
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            except Forbidden:
                # C5: User hasn't started a conversation with the bot
                logger.warning(
                    "Cannot send to chat_id=%s: user has not sent /start to the bot. "
                    "The bot cannot initiate conversations until the user sends /start.",
                    chat_id,
                )
            except Exception as e:
                logger.error("Failed to send to chat_id=%s: %s", chat_id, e)

    def setup(self) -> None:
        """Register all scheduled jobs."""
        rules = self.settings.rules
        email_config = rules.get("email", {})
        health_config = rules.get("health", {})
        tz = self.settings.timezone

        # Email briefing jobs
        check_times = email_config.get("check_times", ["07:00", "20:00"])
        for check_time in check_times:
            hour, minute = map(int, check_time.split(":"))
            self.scheduler.add_job(
                self._email_briefing_job,
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                id=f"email_check_{check_time}",
                name=f"Email check at {check_time}",
                replace_existing=True,
            )

        # Reminder check - every 60 seconds
        self.scheduler.add_job(
            self._reminder_check_job,
            IntervalTrigger(seconds=60),
            id="reminder_check",
            name="Reminder check",
            replace_existing=True,
        )

        # Snoozed reminder check - every 60 seconds
        self.scheduler.add_job(
            self._snoozed_reminder_job,
            IntervalTrigger(seconds=60),
            id="snoozed_reminder_check",
            name="Snoozed reminder check",
            replace_existing=True,
        )

        # C4: Heartbeat file - every 60 seconds
        self.scheduler.add_job(
            self._heartbeat_file_job,
            IntervalTrigger(seconds=60),
            id="heartbeat_file",
            name="Write heartbeat file",
            replace_existing=True,
        )

        # Daily heartbeat
        heartbeat_time = health_config.get("heartbeat_time", "08:00")
        hb_hour, hb_minute = map(int, heartbeat_time.split(":"))
        self.scheduler.add_job(
            self._heartbeat_job,
            CronTrigger(hour=hb_hour, minute=hb_minute, timezone=tz),
            id="heartbeat",
            name="Daily heartbeat",
            replace_existing=True,
        )

        # Daily S3 backup
        backup_time = health_config.get("backup_time", "03:00")
        bk_hour, bk_minute = map(int, backup_time.split(":"))
        self.scheduler.add_job(
            self._backup_job,
            CronTrigger(hour=bk_hour, minute=bk_minute, timezone=tz),
            id="backup",
            name="Daily S3 backup",
            replace_existing=True,
        )

        # OAuth expiry re-alert (daily at noon if paused)
        self.scheduler.add_job(
            self._oauth_alert_job,
            CronTrigger(hour=12, minute=0, timezone=tz),
            id="oauth_alert",
            name="OAuth expiry re-alert",
            replace_existing=True,
        )

        logger.info("Scheduler jobs registered")

    def start(self) -> None:
        """Start the scheduler."""
        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    # ---- Job Implementations ----

    async def _email_briefing_job(self) -> None:
        """Fetch and send email briefing."""
        logger.info("Running email briefing job")
        try:
            emails = await self.email.process_emails()
            text = formatters.format_briefing(emails, self.settings.timezone)
            await self._send_to_all(text)

            # Send action keyboards for each important email
            for i, email in enumerate(emails):
                if email.get("summary"):
                    await self._send_to_all(
                        f"Actions for: {email['subject']}",
                        reply_markup=keyboards.briefing_actions(i),
                    )
        except EmailServiceUnavailable as e:
            await self._send_to_all(
                f"Email service unavailable: {e}\n"
                "Re-authenticate via scripts/reauth_gmail.py or "
                "scripts/setup_gmail.py."
            )
        except Exception as e:
            logger.error("Email briefing job failed: %s", e)

    async def _reminder_check_job(self) -> None:
        """Check for due reminders and send notifications.

        C2: After sending notification, marks reminder as "notified"
        so it will not be picked up again on the next cycle.

        Pre-alert reminders (source="pre_alert") are formatted differently:
        they look up the parent event via source_ref and show the parent's
        due time instead of the alert's own due time.
        """
        self.reminders.cleanup_expired_pre_alerts()

        if self.reminders.is_quiet_hours():
            return

        due = self.reminders.get_due_reminders()
        for reminder in due:
            if reminder.source == "pre_alert" and reminder.source_ref:
                # Look up the parent event
                try:
                    parent_id = int(reminder.source_ref)
                    parent = self.reminders.get_reminder(parent_id)
                except (ValueError, TypeError):
                    parent = None

                if parent:
                    text = formatters.format_pre_alert(
                        reminder, parent, self.settings.timezone
                    )
                    markup = keyboards.pre_alert_actions(
                        reminder.id, parent.id
                    )
                else:
                    # Parent not found, fall back to standard format
                    text = formatters.format_reminder(
                        reminder, self.settings.timezone
                    )
                    markup = keyboards.reminder_actions(reminder.id)
            else:
                text = formatters.format_reminder(
                    reminder, self.settings.timezone
                )
                markup = keyboards.reminder_actions(reminder.id)

            await self._send_to_all(text, reply_markup=markup)
            # C2: Mark as notified so it won't fire again
            self.reminders.mark_notified(reminder.id)


    async def _snoozed_reminder_job(self) -> None:
        """Re-activate snoozed reminders whose snooze has expired."""
        expired = self.reminders.get_snoozed_due()
        for reminder in expired:
            # Re-activate by setting status back to active
            from src.db.database import get_session
            from src.db.models import Reminder

            with get_session() as session:
                r = session.get(Reminder, reminder.id)
                if r:
                    r.status = "active"

            # It will be picked up by the next reminder_check_job cycle

    async def _heartbeat_file_job(self) -> None:
        """Write heartbeat timestamp to file for Docker HEALTHCHECK.

        C4: The HEALTHCHECK command reads this file and verifies it
        was modified within the last 2 minutes.
        """
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except Exception as e:
            logger.error("Failed to write heartbeat file: %s", e)

    async def _heartbeat_job(self) -> None:
        """Send daily heartbeat to user."""
        logger.info("Sending daily heartbeat")
        groq_status = (
            "CIRCUIT_OPEN" if self.llm.circuit_breaker.is_open else "OK"
        )
        gmail_status = (
            f"DISCONNECTED -- {self.email.pause_reason}"
            if self.email.is_paused
            else "Connected"
        )
        check_times = self.settings.rules.get("email", {}).get(
            "check_times", ["07:00", "20:00"]
        )

        text = self.health.format_heartbeat(
            groq_status=groq_status,
            groq_429_count=self.llm.rate_limit_count_24h,
            gmail_status=gmail_status,
            email_check_times=check_times,
        )
        await self._send_to_all(text)

    async def _backup_job(self) -> None:
        """Run S3 backup."""
        logger.info("Running daily S3 backup")
        success = await self.backup.run_backup()
        if not success:
            await self._send_to_all(
                "S3 backup failed! Check logs for details."
            )

    async def _oauth_alert_job(self) -> None:
        """Re-alert if email service is paused due to OAuth expiry."""
        if self.email.is_paused:
            await self._send_to_all(
                f"Reminder: Email monitoring is still paused.\n"
                f"Reason: {self.email.pause_reason}\n\n"
                "Please re-authenticate:\n"
                "1. Run scripts/reauth_gmail.py on the server, or\n"
                "2. Run scripts/setup_gmail.py locally and upload the token"
            )
