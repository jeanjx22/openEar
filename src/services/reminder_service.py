"""Reminder service with quiet hours, snooze, and recurrence.

All times are stored and compared in UTC. The quiet hours check
converts UTC to the user's local timezone for comparison.
Quiet hours spanning midnight are handled correctly:
    if start > end: is_quiet = (now >= start or now < end)

Two kinds of records:
- Reminders (entities): have status active/notified/completed/snoozed.
  C2: mark_notified() prevents re-firing; user actions transition from
  "notified".
- Alerts (one-shot notifications, source="pre_alert"): created with
  status "active", then DELETED after firing via delete_fired_alert().
  Immutable -- only created or deleted, never modified.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.config import Settings
from src.db.database import get_session
from src.db.models import Reminder

logger = logging.getLogger(__name__)


class ReminderService:
    """Service for managing reminders."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        rules = settings.rules.get("reminders", {})
        self._default_snooze = rules.get("default_snooze", "1h")
        quiet = rules.get("quiet_hours", ["22:00", "07:00"])
        self._quiet_start = time.fromisoformat(quiet[0])
        self._quiet_end = time.fromisoformat(quiet[1])
        self._tz = ZoneInfo(settings.timezone)

    def create_reminder(
        self,
        title: str,
        due_at: datetime,
        description: str | None = None,
        recurrence: str | None = None,
        source: str = "user_manual",
        source_ref: str | None = None,
        alert_label: str | None = None,
        chat_id: int | None = None,
    ) -> Reminder:
        """Create a new reminder. due_at must be UTC."""
        with get_session() as session:
            reminder = Reminder(
                title=title,
                description=description,
                due_at=due_at,
                recurrence=recurrence,
                source=source,
                source_ref=source_ref,
                alert_label=alert_label,
                chat_id=chat_id,
                status="active",
            )
            session.add(reminder)
            session.flush()
            # C1: expunge before session closes so object remains usable
            session.expunge(reminder)
            logger.info("Created reminder #%d: %s at %s", reminder.id, title, due_at)
        return reminder

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        """Get a reminder by ID."""
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                # C1: expunge before session closes
                session.expunge(reminder)
            return reminder

    def get_active_reminders(self) -> list[Reminder]:
        """Get all active or notified (non-completed, non-snoozed) reminders."""
        with get_session() as session:
            stmt = select(Reminder).where(Reminder.status.in_(["active", "notified"]))
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for r in results:
                session.expunge(r)
            return results

    def get_future_reminders(self) -> list[Reminder]:
        """Get parent reminders that are in the future or recently notified.

        Returns non-pre_alert reminders where:
        - due_at is in the future (regardless of status), OR
        - status is "notified" (just fired, not yet acknowledged)

        Excludes completed and past events that were never acknowledged.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.source != "pre_alert")
                .where(
                    (Reminder.due_at > now)
                    | (Reminder.status == "notified")
                )
                .where(Reminder.status.in_(["active", "notified"]))
                .order_by(Reminder.due_at.asc())
            )
            results = list(session.execute(stmt).scalars().all())
            for r in results:
                session.expunge(r)
            return results

    def get_due_reminders(self) -> list[Reminder]:
        """Get reminders that are due now (due_at <= now, status=active).

        C2: Only returns reminders with status="active". Once a reminder
        is notified via mark_notified(), it will not appear here again.
        Uses naive UTC for SQLite compatibility.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None).replace(tzinfo=None)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.status == "active")
                .where(Reminder.due_at <= now)
            )
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for r in results:
                session.expunge(r)
            return results

    def get_snoozed_due(self) -> list[Reminder]:
        """Get snoozed reminders whose snooze has expired."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.status == "snoozed")
                .where(Reminder.snoozed_until <= now)
            )
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for r in results:
                session.expunge(r)
            return results

    def mark_notified(self, reminder_id: int) -> None:
        """Mark a reminder as notified after sending notification.

        C2: Transitions status from "active" to "notified" so it will
        not be picked up by get_due_reminders() again. The user must
        take action (Done/Snooze) to transition from "notified".
        """
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder and reminder.status == "active":
                reminder.status = "notified"
                logger.info("Reminder #%d marked as notified", reminder_id)

    def complete_reminder(self, reminder_id: int) -> Reminder | None:
        """Mark a reminder as completed. If recurring, schedule next occurrence.

        C2: Accepts reminders in "active" or "notified" status.
        """
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if not reminder:
                return None
            reminder.status = "completed"

            # Handle recurrence
            if reminder.recurrence:
                next_due = self._calculate_next_due(
                    reminder.due_at, reminder.recurrence
                )
                new_reminder = Reminder(
                    title=reminder.title,
                    description=reminder.description,
                    due_at=next_due,
                    recurrence=reminder.recurrence,
                    source=reminder.source,
                    source_ref=reminder.source_ref,
                    status="active",
                )
                session.add(new_reminder)
                logger.info(
                    "Created recurring reminder: %s at %s",
                    new_reminder.title,
                    next_due,
                )

            # Flush so the status change is persisted before expunge
            session.flush()
            # C1: expunge before session closes
            session.expunge(reminder)
            return reminder

    def snooze_reminder(
        self, reminder_id: int, duration: str = ""
    ) -> Reminder | None:
        """Snooze a reminder for the given duration.

        Duration format: "1h", "30m", "1d", "tomorrow".
        Empty string uses the configured default.
        C2: Accepts reminders in "active" or "notified" status.
        """
        if not duration:
            duration = self._default_snooze

        delta = self._parse_duration(duration)
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if not reminder:
                return None
            reminder.status = "snoozed"
            reminder.snoozed_until = datetime.now(timezone.utc) + delta
            # Flush so the status change is persisted before expunge
            session.flush()
            # C1: expunge before session closes
            session.expunge(reminder)
            return reminder

    def is_quiet_hours(self) -> bool:
        """Check if current local time is within quiet hours.

        Handles midnight wraparound:
        If quiet_start > quiet_end (e.g., 22:00 to 07:00),
        then is_quiet = (now >= start OR now < end).
        """
        now_local = datetime.now(self._tz).time()

        if self._quiet_start > self._quiet_end:
            # Spans midnight
            return now_local >= self._quiet_start or now_local < self._quiet_end
        else:
            return self._quiet_start <= now_local < self._quiet_end

    def next_morning_utc(self) -> datetime:
        """Return the UTC datetime for the next quiet_end in local time."""
        now_local = datetime.now(self._tz)
        next_morning = now_local.replace(
            hour=self._quiet_end.hour,
            minute=self._quiet_end.minute,
            second=0,
            microsecond=0,
        )
        if next_morning <= now_local:
            next_morning += timedelta(days=1)
        return next_morning.astimezone(timezone.utc)

    def delete_fired_alert(self, reminder_id: int) -> bool:
        """Delete a pre-alert record from DB after it has been fired.

        Alerts are immutable one-shot notifications: once fired, they are
        deleted rather than transitioned to a different status.
        Returns True if found and deleted, False otherwise.
        """
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder and reminder.source == "pre_alert":
                session.delete(reminder)
                logger.info("Deleted fired alert #%d", reminder_id)
                return True
            return False

    def cleanup_expired_pre_alerts(self) -> int:
        """Safety-net cleanup for pre-alerts that were not deleted after firing.

        Only deletes pre-alerts whose due_at is more than 1 hour in the past.
        Under normal operation alerts are deleted immediately after firing via
        delete_fired_alert(). This catches stragglers from crashes or restarts.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(hours=1)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.source == "pre_alert")
                .where(Reminder.due_at < cutoff)
                .where(Reminder.status.in_(["active", "notified"]))
            )
            expired = list(session.execute(stmt).scalars().all())
            for r in expired:
                session.delete(r)
            session.flush()
            if expired:
                logger.info(
                    "Safety-net cleanup: deleted %d stale pre-alerts", len(expired)
                )
            return len(expired)

    def auto_complete_past_events(self, grace_hours: int = 2) -> int:
        """Mark non-pre_alert reminders as completed when their due_at
        is more than grace_hours in the past and status is active or notified.

        This prevents stale events from lingering in the active list
        long after they have passed.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(hours=grace_hours)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.source != "pre_alert")
                .where(Reminder.due_at < cutoff)
                .where(Reminder.status.in_(["active", "notified"]))
            )
            expired = list(session.execute(stmt).scalars().all())
            for r in expired:
                r.status = "completed"
                logger.info(
                    "Auto-completed past event #%d: %s (due %s)",
                    r.id, r.title, r.due_at,
                )
            session.flush()
            return len(expired)

    def get_active_pre_alerts(self) -> list[Reminder]:
        """Get all active pre-alert reminders (source='pre_alert', status='active').

        Used on startup to re-schedule alert jobs from persistent DB state.
        """
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.source == "pre_alert")
                .where(Reminder.status == "active")
                .order_by(Reminder.due_at.asc())
            )
            results = list(session.execute(stmt).scalars().all())
            for r in results:
                session.expunge(r)
            return results

    def get_alerts_for_reminder(self, parent_id: int) -> list[Reminder]:
        """Get active pre-alert reminders associated with a parent reminder."""
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.source == "pre_alert")
                .where(Reminder.source_ref == str(parent_id))
                .where(Reminder.status.in_(["active", "notified"]))
                .order_by(Reminder.due_at.asc())
            )
            results = list(session.execute(stmt).scalars().all())
            for r in results:
                session.expunge(r)
            return results

    def get_past_unresolved(self, hours: int = 2) -> list[Reminder]:
        """Get reminders where due_at is in the past (within last N hours),
        status is 'active' or 'notified', and source is NOT 'pre_alert'.

        These are main events that have passed without being marked done.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(hours=hours)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.due_at < now)
                .where(Reminder.due_at >= cutoff)
                .where(Reminder.status.in_(["active", "notified"]))
                .where(Reminder.source != "pre_alert")
            )
            results = list(session.execute(stmt).scalars().all())
            for r in results:
                session.expunge(r)
            return results

    def update_reminder_time(self, reminder_id: int, new_due_at: datetime) -> Reminder | None:
        """Update the due_at of an existing reminder and delete its pre-alerts.

        Deletes all existing pre-alerts for this reminder, updates the due time,
        and resets the status to active. new_due_at must be UTC.
        Returns the updated reminder, or None if not found.
        """
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if not reminder:
                return None

            # Delete existing pre-alerts for this reminder
            stmt = (
                select(Reminder)
                .where(Reminder.source == "pre_alert")
                .where(Reminder.source_ref == str(reminder_id))
            )
            old_alerts = list(session.execute(stmt).scalars().all())
            for alert in old_alerts:
                session.delete(alert)

            # Update the reminder time and reset status
            reminder.due_at = new_due_at
            reminder.status = "active"
            session.flush()
            session.expunge(reminder)
            logger.info(
                "Updated reminder #%d time to %s (deleted %d pre-alerts)",
                reminder_id, new_due_at, len(old_alerts),
            )
        return reminder

    def delete_reminder(self, reminder_id: int) -> bool:
        """Delete a reminder by ID. Returns True if found and deleted."""
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                session.delete(reminder)
                logger.info("Deleted reminder #%d", reminder_id)
                return True
            return False

    def _calculate_next_due(self, current_due: datetime, recurrence: str) -> datetime:
        """Calculate the next due date based on recurrence pattern."""
        if recurrence == "daily":
            return current_due + timedelta(days=1)
        elif recurrence == "weekly":
            return current_due + timedelta(weeks=1)
        elif recurrence == "monthly":
            # Approximate: add 30 days
            return current_due + timedelta(days=30)
        else:
            # Default to daily for unknown patterns
            return current_due + timedelta(days=1)

    def _parse_duration(self, duration: str) -> timedelta:
        """Parse a duration string like '1h', '30m', '1d', 'tomorrow'."""
        duration = duration.strip().lower()
        if duration == "tomorrow":
            return timedelta(days=1)
        if duration.endswith("h"):
            return timedelta(hours=int(duration[:-1]))
        if duration.endswith("m"):
            return timedelta(minutes=int(duration[:-1]))
        if duration.endswith("d"):
            return timedelta(days=int(duration[:-1]))
        # Default: 1 hour
        return timedelta(hours=1)
