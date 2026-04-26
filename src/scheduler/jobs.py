"""APScheduler job definitions for openEar.

Jobs:
- Email briefing: runs at configured check_times
- Reminder check: runs every minute to fire due REGULAR reminders
- Alert jobs: one-time DateTrigger jobs that fire at the exact alert time
- Heartbeat: runs daily at configured heartbeat_time
- Backup: runs daily at configured backup_time
- Snoozed reminders: runs every minute to wake expired snoozes
- Heartbeat file: writes /tmp/openear_heartbeat every 60s (C4)

Alert scheduling model:
- Pre-alerts (source="pre_alert") are scheduled as one-time DateTrigger
  jobs via schedule_alert(). Each fires at the exact due_at time, sends
  the notification, and deletes the alert record from the DB.
- On startup, reschedule_alerts_from_db() restores jobs from the DB.
- misfire_grace_time=3600 ensures alerts fire even after sleep/wake
  (up to 1 hour late).
- Regular reminders still use the 60-second polling loop and are marked
  "notified" (C2) so the user can act on them.

C4: _heartbeat_file_job writes heartbeat file for Docker HEALTHCHECK.
C5: _send_to_all catches Forbidden errors with clear warning.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
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

    async def _send_to_all(self, text: str, reply_markup=None) -> None:
        """Send a message to all active chats (DMs and group chats).

        Uses active_chat_ids tracked by handlers -- this includes every
        chat (DM or group) where an authorized user has interacted with
        the bot.  Falls back to telegram_allowed_user_ids if no chats
        have been recorded yet (e.g., right after a restart before any
        user sends a message).

        C5: Catches Forbidden errors (user hasn't sent /start to the bot)
        and logs a clear warning instead of silently failing.
        """
        chat_ids = self.app.bot_data.get("active_chat_ids", set())
        if not chat_ids:
            # Fallback: no user has interacted yet since restart
            chat_ids = self.settings.telegram_allowed_user_ids
        for chat_id in chat_ids:
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
        # misfire_grace_time=None means always run even after sleep/wake
        self.scheduler.add_job(
            self._reminder_check_job,
            IntervalTrigger(seconds=60),
            id="reminder_check",
            name="Reminder check",
            replace_existing=True,
            misfire_grace_time=None,
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
        """Start the scheduler and re-schedule alerts from DB."""
        self.scheduler.start()
        self.reschedule_alerts_from_db()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    # ---- Direct Alert Scheduling ----

    def schedule_alert(self, alert_reminder, parent_reminder) -> None:
        """Schedule a one-time job to fire a pre-alert at its exact due time.

        Uses DateTrigger so the notification fires at the precise second
        rather than waiting up to 60 seconds for a poll cycle.

        Args:
            alert_reminder: The pre-alert Reminder record (source="pre_alert").
            parent_reminder: The parent Reminder this alert belongs to.
        """
        job_id = f"alert_{alert_reminder.id}"
        run_date = alert_reminder.due_at
        # Ensure timezone-aware for APScheduler
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=timezone.utc)

        self.scheduler.add_job(
            self._fire_alert_job,
            DateTrigger(run_date=run_date),
            id=job_id,
            name=f"Alert: {alert_reminder.title}",
            replace_existing=True,
            misfire_grace_time=3600,
            kwargs={
                "alert_id": alert_reminder.id,
                "parent_id": parent_reminder.id,
            },
        )
        logger.info(
            "Scheduled alert job %s for %s (alert #%d -> parent #%d)",
            job_id, run_date, alert_reminder.id, parent_reminder.id,
        )

    def cancel_alert(self, alert_id: int) -> None:
        """Remove a scheduled alert job by ID.

        Called when the user deletes an alert. Silently ignores
        if the job does not exist (already fired or never scheduled).
        """
        job_id = f"alert_{alert_id}"
        try:
            self.scheduler.remove_job(job_id)
            logger.info("Cancelled alert job %s", job_id)
        except Exception:
            # Job may have already fired or never been scheduled
            logger.debug("Alert job %s not found (already fired or missing)", job_id)

    def reschedule_alerts_from_db(self) -> None:
        """Re-schedule all active pre-alerts from the database.

        Called on startup to restore alert jobs after a bot restart.
        Past-due alerts with misfire_grace_time=3600 will fire immediately
        if they are less than 1 hour old.
        """
        alerts = self.reminders.get_active_pre_alerts()
        scheduled = 0
        for alert in alerts:
            try:
                parent_id = int(alert.source_ref) if alert.source_ref else None
            except (ValueError, TypeError):
                parent_id = None

            if parent_id is None:
                logger.warning(
                    "Skipping alert #%d: no valid parent_id (source_ref=%s)",
                    alert.id, alert.source_ref,
                )
                continue

            parent = self.reminders.get_reminder(parent_id)
            if parent is None:
                # Parent was deleted; clean up orphaned alert
                self.reminders.delete_fired_alert(alert.id)
                logger.info(
                    "Deleted orphaned alert #%d (parent #%d not found)",
                    alert.id, parent_id,
                )
                continue

            self.schedule_alert(alert, parent)
            scheduled += 1

        logger.info(
            "Startup: re-scheduled %d alert(s) from database", scheduled
        )

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

    async def _fire_alert_job(self, alert_id: int, parent_id: int) -> None:
        """Fire a single pre-alert notification (called by DateTrigger).

        Sends the notification and deletes the alert record from the DB.
        This replaces the old polling approach for pre-alerts.
        """
        alert = self.reminders.get_reminder(alert_id)
        if not alert:
            logger.info("Alert #%d already deleted, skipping fire", alert_id)
            return

        if alert.source != "pre_alert":
            logger.warning("Job called for non-alert #%d, skipping", alert_id)
            return

        parent = self.reminders.get_reminder(parent_id)
        if parent:
            text = formatters.format_pre_alert(
                alert, parent, self.settings.timezone
            )
            markup = keyboards.pre_alert_actions(alert.id, parent.id)
        else:
            text = formatters.format_reminder(alert, self.settings.timezone)
            markup = keyboards.reminder_actions(alert.id)

        await self._send_to_all(text, reply_markup=markup)
        self.reminders.delete_fired_alert(alert.id)
        logger.info("Fired and deleted alert #%d (parent #%d)", alert_id, parent_id)

    async def _reminder_check_job(self) -> None:
        """Check for due REGULAR reminders and send notifications.

        Pre-alerts are no longer handled here -- they fire via
        DateTrigger jobs scheduled at the exact due time (see
        schedule_alert / _fire_alert_job).

        Regular reminders are marked as "notified" (C2) so they won't
        fire again but remain in the DB for user actions (Done/Snooze).
        """
        if self.reminders.is_quiet_hours():
            return

        due = self.reminders.get_due_reminders()
        for reminder in due:
            # Skip pre-alerts -- they are handled by DateTrigger jobs
            if reminder.source == "pre_alert":
                continue

            text = formatters.format_reminder(
                reminder, self.settings.timezone
            )
            markup = keyboards.reminder_actions(reminder.id)
            await self._send_to_all(text, reply_markup=markup)
            # C2: Mark as notified so it won't fire again
            self.reminders.mark_notified(reminder.id)

        # Periodic maintenance
        self.reminders.auto_complete_past_events(grace_hours=2)
        # Safety net: delete stale alerts that survived a crash/restart
        self.reminders.cleanup_expired_pre_alerts()

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
