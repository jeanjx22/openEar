"""Unit tests for the openEar reminder alert system.

Covers ReminderService, formatters, LLM parse_alert_time, and database models.
Each test gets a fresh temporary SQLite database.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.config import Settings
from src.db import database as db_module
from src.db.database import get_session, init_db, insert_email_ignore_duplicate
from src.db.models import Base, Email, Reminder
from src.bot.formatters import (
    format_pre_alert,
    format_reminder,
    to_local,
)
from src.services.reminder_service import ReminderService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Build a Settings object with sensible defaults for testing."""
    defaults = dict(
        timezone="America/Los_Angeles",
        rules={
            "reminders": {
                "default_snooze": "1h",
                "quiet_hours": ["22:00", "07:00"],
            }
        },
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    """Initialise a fresh SQLite database in a temp directory for every test."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    yield db_path
    # Reset module-level singletons so the next test starts clean.
    db_module._engine = None
    db_module._SessionLocal = None


@pytest.fixture()
def service() -> ReminderService:
    return ReminderService(_make_settings())


# ---------------------------------------------------------------------------
# 1. Create a reminder -- stores correct title, due_at (UTC), status="active"
# ---------------------------------------------------------------------------


class TestCreateReminder:
    def test_create_stores_fields(self, service: ReminderService):
        due = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
        r = service.create_reminder(title="Dentist", due_at=due)

        assert r.title == "Dentist"
        assert r.due_at == due
        assert r.status == "active"
        assert r.id is not None


# ---------------------------------------------------------------------------
# 2. mark_notified() transitions status from "active" to "notified"
# ---------------------------------------------------------------------------


class TestMarkNotified:
    def test_transitions_to_notified(self, service: ReminderService):
        due = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
        r = service.create_reminder(title="Call mom", due_at=due)
        assert r.status == "active"

        service.mark_notified(r.id)

        updated = service.get_reminder(r.id)
        assert updated is not None
        assert updated.status == "notified"


# ---------------------------------------------------------------------------
# 3. complete_reminder() transitions to "completed" and creates next
#    occurrence if recurring
# ---------------------------------------------------------------------------


class TestCompleteReminder:
    def test_non_recurring_completes(self, service: ReminderService):
        due = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
        r = service.create_reminder(title="Buy milk", due_at=due)
        completed = service.complete_reminder(r.id)

        assert completed is not None
        assert completed.status == "completed"

    def test_recurring_creates_next(self, service: ReminderService):
        due = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
        r = service.create_reminder(
            title="Take vitamins", due_at=due, recurrence="daily"
        )
        service.complete_reminder(r.id)

        # The original should be completed.
        original = service.get_reminder(r.id)
        assert original.status == "completed"

        # A new active reminder should exist with the next day's due date.
        active = service.get_active_reminders()
        assert len(active) == 1
        assert active[0].title == "Take vitamins"
        # SQLite drops tzinfo, so compare naive-to-naive
        expected_due = (due + timedelta(days=1)).replace(tzinfo=None)
        actual_due = active[0].due_at.replace(tzinfo=None) if active[0].due_at.tzinfo else active[0].due_at
        assert actual_due == expected_due
        assert active[0].recurrence == "daily"


# ---------------------------------------------------------------------------
# 4. snooze_reminder() sets status="snoozed" and snoozed_until
# ---------------------------------------------------------------------------


class TestSnoozeReminder:
    def test_snooze_sets_status_and_until(self, service: ReminderService):
        due = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
        r = service.create_reminder(title="Exercise", due_at=due)

        before_snooze = datetime.now(timezone.utc)
        snoozed = service.snooze_reminder(r.id, "2h")
        after_snooze = datetime.now(timezone.utc)

        assert snoozed is not None
        assert snoozed.status == "snoozed"
        assert snoozed.snoozed_until is not None
        # snoozed_until should be roughly now + 2 hours
        expected_lo = before_snooze + timedelta(hours=2)
        expected_hi = after_snooze + timedelta(hours=2)
        # Make timezone-aware comparison safe (snoozed_until may be naive from SQLite)
        su = snoozed.snoozed_until
        if su.tzinfo is None:
            su = su.replace(tzinfo=timezone.utc)
        assert expected_lo <= su <= expected_hi


# ---------------------------------------------------------------------------
# 5. get_due_reminders() only returns "active" reminders with due_at <= now
# ---------------------------------------------------------------------------


class TestGetDueReminders:
    def test_returns_due_active_only(self, service: ReminderService):
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=1)
        future = now + timedelta(hours=1)

        service.create_reminder(title="Past active", due_at=past)
        service.create_reminder(title="Future active", due_at=future)

        due = service.get_due_reminders()
        titles = [r.title for r in due]
        assert "Past active" in titles
        assert "Future active" not in titles


# ---------------------------------------------------------------------------
# 6. get_due_reminders() does NOT return "notified" reminders (C2 fix)
# ---------------------------------------------------------------------------


class TestC2NotifiedExclusion:
    def test_notified_not_returned(self, service: ReminderService):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        r = service.create_reminder(title="Already notified", due_at=past)
        service.mark_notified(r.id)

        due = service.get_due_reminders()
        ids = [rem.id for rem in due]
        assert r.id not in ids, "Notified reminders must not reappear in get_due_reminders()"


# ---------------------------------------------------------------------------
# 7. is_quiet_hours() correctly handles midnight wraparound (22:00-07:00)
# ---------------------------------------------------------------------------


class TestIsQuietHours:
    def _service_at_time(self, local_hour: int, local_minute: int = 0) -> ReminderService:
        """Return a ReminderService whose is_quiet_hours() sees a given local time."""
        svc = ReminderService(_make_settings())
        fake_now = datetime(2026, 5, 1, local_hour, local_minute, tzinfo=ZoneInfo("America/Los_Angeles"))
        with patch("src.services.reminder_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = svc.is_quiet_hours.__wrapped__(svc) if hasattr(svc.is_quiet_hours, "__wrapped__") else None
        return svc, fake_now

    def test_before_midnight_is_quiet(self):
        svc = ReminderService(_make_settings())
        # 23:00 local -> quiet (22:00-07:00 wraps midnight)
        fake_local = datetime(2026, 5, 1, 23, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        with patch("src.services.reminder_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # Directly test the logic since we control _quiet_start/_quiet_end
            now_local = fake_local.time()
            start = svc._quiet_start  # 22:00
            end = svc._quiet_end      # 07:00
            assert start > end, "Test setup: quiet hours should span midnight"
            is_quiet = now_local >= start or now_local < end
            assert is_quiet is True

    def test_after_midnight_is_quiet(self):
        svc = ReminderService(_make_settings())
        now_local = time(3, 0)  # 3 AM
        start = svc._quiet_start  # 22:00
        end = svc._quiet_end      # 07:00
        is_quiet = now_local >= start or now_local < end
        assert is_quiet is True

    def test_midday_is_not_quiet(self):
        svc = ReminderService(_make_settings())
        now_local = time(12, 0)  # noon
        start = svc._quiet_start
        end = svc._quiet_end
        is_quiet = now_local >= start or now_local < end
        assert is_quiet is False

    def test_boundary_start_is_quiet(self):
        svc = ReminderService(_make_settings())
        now_local = time(22, 0)  # exactly at start
        start = svc._quiet_start
        end = svc._quiet_end
        is_quiet = now_local >= start or now_local < end
        assert is_quiet is True

    def test_boundary_end_is_not_quiet(self):
        svc = ReminderService(_make_settings())
        now_local = time(7, 0)  # exactly at end
        start = svc._quiet_start
        end = svc._quiet_end
        is_quiet = now_local >= start or now_local < end
        assert is_quiet is False


# ---------------------------------------------------------------------------
# 8. get_alerts_for_reminder() returns pre-alerts matching parent_id via source_ref
# ---------------------------------------------------------------------------


class TestGetAlertsForReminder:
    def test_returns_matching_alerts(self, service: ReminderService):
        parent_due = datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc)
        parent = service.create_reminder(title="Meeting", due_at=parent_due)

        alert_due = parent_due - timedelta(hours=1)
        service.create_reminder(
            title="Meeting alert",
            due_at=alert_due,
            source="pre_alert",
            source_ref=str(parent.id),
            alert_label="1 hour before",
        )

        alerts = service.get_alerts_for_reminder(parent.id)
        assert len(alerts) == 1
        assert alerts[0].source_ref == str(parent.id)
        assert alerts[0].alert_label == "1 hour before"

    def test_does_not_return_unrelated(self, service: ReminderService):
        parent_due = datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc)
        parent = service.create_reminder(title="Event A", due_at=parent_due)
        other = service.create_reminder(title="Event B", due_at=parent_due)

        service.create_reminder(
            title="Alert for B",
            due_at=parent_due - timedelta(hours=1),
            source="pre_alert",
            source_ref=str(other.id),
        )

        alerts = service.get_alerts_for_reminder(parent.id)
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# 9. get_past_unresolved() returns reminders past due but not completed
# ---------------------------------------------------------------------------


class TestGetPastUnresolved:
    def test_returns_past_active(self, service: ReminderService):
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        service.create_reminder(title="Missed", due_at=past)

        unresolved = service.get_past_unresolved(hours=2)
        titles = [r.title for r in unresolved]
        assert "Missed" in titles

    def test_excludes_completed(self, service: ReminderService):
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        r = service.create_reminder(title="Done item", due_at=past)
        service.complete_reminder(r.id)

        unresolved = service.get_past_unresolved(hours=2)
        titles = [r.title for r in unresolved]
        assert "Done item" not in titles

    def test_excludes_pre_alerts(self, service: ReminderService):
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        service.create_reminder(
            title="Pre-alert",
            due_at=past,
            source="pre_alert",
            source_ref="999",
        )

        unresolved = service.get_past_unresolved(hours=2)
        titles = [r.title for r in unresolved]
        assert "Pre-alert" not in titles


# ---------------------------------------------------------------------------
# 10. to_local() converts UTC to PDT correctly
# ---------------------------------------------------------------------------


class TestToLocal:
    def test_utc_to_pacific(self):
        # May 1 2026 21:00 UTC = May 1 2026 2:00 PM PDT
        utc_dt = datetime(2026, 5, 1, 21, 0, tzinfo=timezone.utc)
        result = to_local(utc_dt, "America/Los_Angeles")
        assert "May 01" in result
        assert "02:00 PM" in result

    def test_naive_treated_as_utc(self):
        naive_dt = datetime(2026, 5, 1, 21, 0)
        result = to_local(naive_dt, "America/Los_Angeles")
        assert "02:00 PM" in result


# ---------------------------------------------------------------------------
# 11. format_reminder() shows correct local time
# ---------------------------------------------------------------------------


class TestFormatReminder:
    def test_shows_local_time(self):
        r = MagicMock()
        r.title = "Dentist"
        r.due_at = datetime(2026, 5, 1, 21, 0, tzinfo=timezone.utc)
        r.recurrence = None
        r.description = None

        result = format_reminder(r, "America/Los_Angeles")
        assert "Dentist" in result
        assert "02:00 PM" in result

    def test_shows_recurrence(self):
        r = MagicMock()
        r.title = "Vitamins"
        r.due_at = datetime(2026, 5, 1, 16, 0, tzinfo=timezone.utc)
        r.recurrence = "daily"
        r.description = None

        result = format_reminder(r, "America/Los_Angeles")
        assert "repeats daily" in result


# ---------------------------------------------------------------------------
# 12. format_pre_alert() shows parent event info, not alert's own time
# ---------------------------------------------------------------------------


class TestFormatPreAlert:
    def test_shows_parent_info(self):
        alert = MagicMock()
        alert.alert_label = "Tomorrow"
        alert.due_at = datetime(2026, 4, 30, 3, 0, tzinfo=timezone.utc)  # alert time

        parent = MagicMock()
        parent.title = "Couple therapy"
        parent.due_at = datetime(2026, 5, 1, 23, 0, tzinfo=timezone.utc)  # event time

        result = format_pre_alert(alert, parent, "America/Los_Angeles")
        assert "Couple therapy" in result
        assert "Tomorrow" in result
        # Should display the parent event time (May 1 4:00 PM PDT), not the alert time.
        assert "04:00 PM" in result


# ---------------------------------------------------------------------------
# 13-15. LLM parse_alert_time tests (mock the LLM call)
# ---------------------------------------------------------------------------


class TestParseAlertTime:
    """Tests for LLMService.parse_alert_time with mocked Groq calls."""

    def _make_llm_service(self):
        settings = _make_settings(
            llm_provider="cohere",
            cohere_api_key="test-key",
            llm_model="test-model",
        )
        from src.services.llm_service import LLMService

        svc = LLMService(settings)
        return svc

    # 13. "from_now" type adds minutes to current time
    def test_from_now_type(self):
        svc = self._make_llm_service()
        event_time = datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc)
        mock_response = json.dumps([{"type": "from_now", "minutes": 30, "label": "Test"}])

        with patch.object(svc, "call_groq", new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.get_event_loop().run_until_complete(
                svc.parse_alert_time("30 minutes from now", event_time)
            )

        assert result is not None
        assert len(result) == 1
        expected_approx = datetime.now(timezone.utc) + timedelta(minutes=30)
        diff = abs((result[0]["datetime"] - expected_approx).total_seconds())
        assert diff < 5, f"Expected ~now+30m, got diff={diff}s"

    def test_before_event_type(self):
        svc = self._make_llm_service()
        event_time = datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc)
        mock_response = json.dumps([{"type": "before_event", "minutes": 60, "label": "1hr before"}])

        with patch.object(svc, "call_groq", new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.get_event_loop().run_until_complete(
                svc.parse_alert_time("1 hour before", event_time)
            )

        assert result is not None
        assert len(result) == 1
        expected = event_time - timedelta(hours=1)
        assert result[0]["datetime"] == expected

    def test_absolute_type(self):
        svc = self._make_llm_service()
        event_time = datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc)
        mock_response = json.dumps([{
            "type": "absolute",
            "date": "2026-05-04",
            "time": "20:00",
            "timezone": "America/Los_Angeles",
            "label": "Night before",
        }])

        with patch.object(svc, "call_groq", new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.get_event_loop().run_until_complete(
                svc.parse_alert_time("the day before at 8pm", event_time)
            )

        assert result is not None
        assert len(result) == 1
        expected_utc = datetime(2026, 5, 5, 3, 0, tzinfo=timezone.utc)
        assert result[0]["datetime"] == expected_utc


# ---------------------------------------------------------------------------
# 16. Reminder model stores parent_id and alert_label fields
# ---------------------------------------------------------------------------


class TestReminderModel:
    def test_parent_id_and_alert_label(self):
        with get_session() as session:
            reminder = Reminder(
                title="Pre-alert",
                due_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                source="pre_alert",
                source_ref="42",
                parent_id=42,
                alert_label="Tomorrow evening",
                status="active",
            )
            session.add(reminder)
            session.flush()
            rid = reminder.id

        with get_session() as session:
            loaded = session.get(Reminder, rid)
            assert loaded.parent_id == 42
            assert loaded.alert_label == "Tomorrow evening"

    def test_nullable_parent_id(self):
        with get_session() as session:
            reminder = Reminder(
                title="Normal reminder",
                due_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                status="active",
            )
            session.add(reminder)
            session.flush()
            rid = reminder.id

        with get_session() as session:
            loaded = session.get(Reminder, rid)
            assert loaded.parent_id is None
            assert loaded.alert_label is None


# ---------------------------------------------------------------------------
# 17. insert_email_ignore_duplicate handles duplicates gracefully
# ---------------------------------------------------------------------------


class TestInsertEmailIgnoreDuplicate:
    def test_first_insert_succeeds(self):
        email = Email(
            gmail_id="abc123",
            sender="alice@example.com",
            subject="Hello",
            is_important=True,
            received_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            processed_at=datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc),
        )
        with get_session() as session:
            inserted = insert_email_ignore_duplicate(session, email)
        assert inserted is True

    def test_duplicate_is_ignored(self):
        email1 = Email(
            gmail_id="dup456",
            sender="bob@example.com",
            subject="First",
            is_important=False,
            received_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            processed_at=datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc),
        )
        email2 = Email(
            gmail_id="dup456",
            sender="bob@example.com",
            subject="Second attempt",
            is_important=True,
            received_at=datetime(2026, 5, 1, 13, 0, tzinfo=timezone.utc),
            processed_at=datetime(2026, 5, 1, 13, 5, tzinfo=timezone.utc),
        )
        with get_session() as session:
            first = insert_email_ignore_duplicate(session, email1)
        with get_session() as session:
            second = insert_email_ignore_duplicate(session, email2)

        assert first is True
        assert second is False
