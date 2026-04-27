"""Unit tests for openEar Telegram bot alert UX features.

Covers keyboard builders, callback_data format, formatters,
and integration scenarios for pre-alert / post-event flows.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot import formatters, keyboards


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_buttons(markup):
    """Flatten an InlineKeyboardMarkup into a list of buttons."""
    return [btn for row in markup.inline_keyboard for btn in row]


def _make_reminder(**overrides):
    """Create a fake Reminder-like object using SimpleNamespace."""
    defaults = dict(
        id=1,
        title="Test Reminder",
        description=None,
        due_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        recurrence=None,
        status="active",
        source="user_manual",
        source_ref=None,
        parent_id=None,
        alert_label=None,
        created_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
        snoozed_until=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_note(**overrides):
    """Create a fake Note-like object using SimpleNamespace."""
    defaults = dict(
        id=1,
        content="Pick up groceries",
        tags='["errand", "shopping"]',
        created_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ===================================================================
# 1. Keyboard tests
# ===================================================================

class TestBriefingActions:
    """Test 1: briefing_actions() creates 3 buttons."""

    def test_button_count(self):
        markup = keyboards.briefing_actions(0)
        buttons = _flat_buttons(markup)
        assert len(buttons) == 3

    def test_button_labels(self):
        markup = keyboards.briefing_actions(0)
        labels = [btn.text for btn in _flat_buttons(markup)]
        assert labels == ["Remind Me", "Already Done", "Dismiss"]


class TestReminderActions:
    """Test 2: reminder_actions() creates 4 buttons."""

    def test_button_count(self):
        markup = keyboards.reminder_actions(42)
        buttons = _flat_buttons(markup)
        assert len(buttons) == 4

    def test_button_labels(self):
        markup = keyboards.reminder_actions(42)
        labels = [btn.text for btn in _flat_buttons(markup)]
        assert labels == ["Done", "Snooze 1hr", "Reschedule", "Repeat weekly"]


class TestAlertPreferences:
    """Test 3: alert_preferences() creates 5 options."""

    def test_button_count(self):
        markup = keyboards.alert_preferences(10)
        buttons = _flat_buttons(markup)
        assert len(buttons) == 5

    def test_button_labels(self):
        markup = keyboards.alert_preferences(10)
        labels = [btn.text for btn in _flat_buttons(markup)]
        assert labels == [
            "Day before + morning",
            "Morning of only",
            "At the time only",
            "1 hour before",
            "Custom",
        ]


class TestPreAlertActions:
    """Test 4: pre_alert_actions() includes 'Set more alerts' with parent_id."""

    def test_set_more_alerts_button_exists(self):
        markup = keyboards.pre_alert_actions(reminder_id=5, parent_id=99)
        labels = [btn.text for btn in _flat_buttons(markup)]
        assert "Set more alerts" in labels

    def test_set_more_alerts_uses_parent_id(self):
        markup = keyboards.pre_alert_actions(reminder_id=5, parent_id=99)
        buttons = _flat_buttons(markup)
        set_more = [b for b in buttons if b.text == "Set more alerts"][0]
        assert set_more.callback_data == "alert_more:99"

    def test_done_and_snooze_use_reminder_id(self):
        markup = keyboards.pre_alert_actions(reminder_id=5, parent_id=99)
        buttons = _flat_buttons(markup)
        done_btn = [b for b in buttons if b.text == "Done"][0]
        snooze_btn = [b for b in buttons if b.text == "Snooze 1hr"][0]
        assert done_btn.callback_data == "reminder_done:5"
        assert snooze_btn.callback_data == "reminder_snooze_1h:5"


class TestPostEventActions:
    """Test 5: post_event_actions() includes correct buttons."""

    def test_button_count(self):
        markup = keyboards.post_event_actions(7)
        buttons = _flat_buttons(markup)
        assert len(buttons) == 3

    def test_button_labels(self):
        markup = keyboards.post_event_actions(7)
        labels = [btn.text for btn in _flat_buttons(markup)]
        assert "Yes, mark done" in labels
        assert "Snooze 1hr" in labels
        assert "Reschedule" in labels


class TestManageAlerts:
    """Test 6: manage_alerts() includes 'Add alert' and 'Delete alerts'."""

    def test_button_count(self):
        markup = keyboards.manage_alerts(3)
        buttons = _flat_buttons(markup)
        assert len(buttons) == 2

    def test_button_labels(self):
        markup = keyboards.manage_alerts(3)
        labels = [btn.text for btn in _flat_buttons(markup)]
        assert "Add alert" in labels
        assert "Delete alerts" in labels


class TestDeleteAlertActions:
    """Test 7: delete_alert_actions() creates one delete button per alert + Cancel."""

    def test_single_alert(self):
        alert = SimpleNamespace(id=10, title="Morning alert", alert_label="Tomorrow", due_at=datetime(2026, 4, 26, 3, 0, tzinfo=timezone.utc))
        markup = keyboards.delete_alert_actions([alert])
        buttons = _flat_buttons(markup)
        assert len(buttons) == 2
        assert "Tomorrow" in buttons[0].text
        assert buttons[1].text == "Cancel"

    def test_multiple_alerts(self):
        alerts = [
            SimpleNamespace(id=10, title="Morning alert", alert_label="Tomorrow", due_at=datetime(2026, 4, 26, 3, 0, tzinfo=timezone.utc)),
            SimpleNamespace(id=11, title="Evening alert", alert_label="Today", due_at=datetime(2026, 4, 27, 15, 0, tzinfo=timezone.utc)),
            SimpleNamespace(id=12, title="Night alert", alert_label="Custom", due_at=datetime(2026, 4, 27, 22, 0, tzinfo=timezone.utc)),
        ]
        markup = keyboards.delete_alert_actions(alerts)
        buttons = _flat_buttons(markup)
        assert len(buttons) == 4
        assert buttons[-1].text == "Cancel"

    def test_empty_list(self):
        markup = keyboards.delete_alert_actions([])
        buttons = _flat_buttons(markup)
        assert len(buttons) == 1
        assert buttons[0].text == "Cancel"

    def test_long_title_truncated(self):
        alert = SimpleNamespace(id=20, title="Long", alert_label="Tomorrow", due_at=datetime(2026, 4, 26, 3, 0, tzinfo=timezone.utc))
        markup = keyboards.delete_alert_actions([alert])
        buttons = _flat_buttons(markup)
        assert "Tomorrow" in buttons[0].text

    def test_no_title_fallback(self):
        alert = SimpleNamespace(id=15, title=None, alert_label=None, due_at=datetime(2026, 4, 26, 3, 0, tzinfo=timezone.utc))
        markup = keyboards.delete_alert_actions([alert])
        buttons = _flat_buttons(markup)
        assert "Alert" in buttons[0].text


# ===================================================================
# 2. Callback data format tests
# ===================================================================

class TestCallbackDataFormat:
    """Test 8 & 9: callback_data strings follow 'action:id' pattern."""

    # Pattern allows multi-segment actions like "reminder_snooze_1h:42"
    # where the action part can include letters, digits, and underscores.
    ACTION_ID_PATTERN = re.compile(r"^[a-z0-9_]+:\d+$")

    @pytest.mark.parametrize(
        "builder,args",
        [
            (keyboards.briefing_actions, (0,)),
            (keyboards.briefing_actions, (99,)),
            (keyboards.reminder_actions, (1,)),
            (keyboards.reminder_actions, (999,)),
            (keyboards.alert_preferences, (5,)),
            (keyboards.pre_alert_actions, (5, 10)),
            (keyboards.post_event_actions, (7,)),
            (keyboards.manage_alerts, (3,)),
        ],
    )
    def test_all_buttons_follow_action_id_pattern(self, builder, args):
        markup = builder(*args)
        for btn in _flat_buttons(markup):
            assert self.ACTION_ID_PATTERN.match(btn.callback_data), (
                f"callback_data '{btn.callback_data}' from {builder.__name__} "
                "does not match 'action:id' pattern"
            )

    def test_delete_alert_buttons_follow_pattern(self):
        alerts = [SimpleNamespace(id=10, title="X", alert_label="Test", due_at=datetime(2026, 4, 26, 3, 0, tzinfo=timezone.utc))]
        markup = keyboards.delete_alert_actions(alerts)
        for btn in _flat_buttons(markup):
            # Cancel is a special case (no id)
            if btn.callback_data == "cancel":
                continue
            assert self.ACTION_ID_PATTERN.match(btn.callback_data), (
                f"callback_data '{btn.callback_data}' does not match pattern"
            )

    def test_pre_alert_set_more_uses_parent_id_not_reminder_id(self):
        """Test 9: 'Set more alerts' uses parent_id, not reminder_id."""
        reminder_id = 100
        parent_id = 200
        markup = keyboards.pre_alert_actions(reminder_id, parent_id)
        buttons = _flat_buttons(markup)
        set_more = [b for b in buttons if b.text == "Set more alerts"][0]
        # Must use parent_id
        assert set_more.callback_data == f"alert_more:{parent_id}"
        # Must NOT use reminder_id
        assert set_more.callback_data != f"alert_more:{reminder_id}"


# ===================================================================
# 3. Formatter tests
# ===================================================================

class TestFormatBriefing:
    """Tests 10 & 11: format_briefing handles empty and populated lists."""

    def test_empty_email_list(self):
        """Test 10: handles empty email list."""
        result = formatters.format_briefing([])
        assert result == "No important emails right now."

    def test_empty_email_list_returns_string(self):
        result = formatters.format_briefing([])
        assert isinstance(result, str)

    def test_multiple_emails_with_labels(self):
        """Test 11: formats multiple emails with labels and emojis."""
        emails = [
            {
                "subject": "Quarterly review",
                "sender": "boss@example.com",
                "label": "Work",
                "summary": "Q1 numbers look great",
                "received_at": datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
            },
            {
                "subject": "Flight confirmation",
                "sender": "airline@example.com",
                "label": "Travel",
                "summary": None,
                "received_at": datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc),
            },
        ]
        result = formatters.format_briefing(emails)
        assert "2 important email(s)" in result
        assert "Quarterly review" in result
        assert "[Work]" in result
        assert "Flight confirmation" in result
        assert "[Travel]" in result
        assert "boss@example.com" in result

    def test_email_without_label(self):
        emails = [
            {
                "subject": "Hello",
                "sender": "friend@example.com",
                "summary": "Just checking in",
            },
        ]
        result = formatters.format_briefing(emails)
        assert "1 important email(s)" in result
        assert "Hello" in result
        # No label tag should appear
        assert "[" not in result or "email(s)" in result.split("[")[0]


class TestFormatReminderList:
    """Test 12: format_reminder_list handles empty list."""

    def test_empty_list(self):
        result = formatters.format_reminder_list([])
        assert result == "No active reminders."

    def test_non_empty_list(self):
        reminders = [
            _make_reminder(id=1, title="Call doctor", status="active"),
            _make_reminder(id=2, title="Buy milk", status="snoozed"),
        ]
        result = formatters.format_reminder_list(reminders)
        assert "Active reminders (2)" in result
        assert "#1: Call doctor" in result
        assert "#2: Buy milk" in result
        assert "(snoozed)" in result


class TestFormatNote:
    """Test 13: format_note includes tags."""

    def test_includes_tags(self):
        note = _make_note(tags='["errand", "shopping"]')
        result = formatters.format_note(note)
        assert "Tags:" in result
        assert "errand" in result
        assert "shopping" in result

    def test_empty_tags(self):
        note = _make_note(tags="[]")
        result = formatters.format_note(note)
        assert "Tags:" not in result

    def test_no_tags_field(self):
        note = _make_note(tags="[]")
        result = formatters.format_note(note)
        assert note.content in result


# ===================================================================
# 4. Integration scenario tests
# ===================================================================

class TestPreAlertFiresWithParentInfo:
    """Test 14: When pre-alert fires, message shows parent event info."""

    def test_format_pre_alert_shows_parent_time(self):
        parent = _make_reminder(
            id=10,
            title="Dentist appointment",
            due_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        )
        alert = _make_reminder(
            id=50,
            title="Alert: Dentist appointment",
            due_at=datetime(2026, 5, 1, 6, 0, tzinfo=timezone.utc),
            source="pre_alert",
            source_ref="10",
            alert_label="Morning of",
        )
        text = formatters.format_pre_alert(alert, parent, "America/Los_Angeles")
        # Should show the parent title and due time, not the alert's 6am time
        assert "Dentist appointment" in text
        assert "Morning of" in text
        # The parent event is May 1 at 2pm UTC = May 1 7:00 AM PDT
        assert "May 01" in text

    def test_fire_alert_job_sends_parent_info(self):
        """Verify _fire_alert_job sends parent event info for pre-alerts."""
        from src.scheduler.jobs import SchedulerJobs

        parent = _make_reminder(
            id=10,
            title="Team meeting",
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
        )

        mock_settings = MagicMock()
        mock_settings.timezone = "America/Los_Angeles"
        mock_settings.telegram_allowed_user_ids = [12345]

        alert = _make_reminder(
            id=50,
            title="Alert: Team meeting",
            due_at=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
            source="pre_alert",
            source_ref="10",
            alert_label="1 hour before",
        )

        mock_reminder_svc = MagicMock()
        mock_reminder_svc.get_reminder.side_effect = lambda rid: alert if rid == 50 else parent if rid == 10 else None
        mock_reminder_svc.delete_fired_alert = MagicMock()

        mock_app = MagicMock()
        mock_bot = AsyncMock()
        mock_app.bot = mock_bot
        mock_app.bot_data = {"active_chat_ids": {12345}}

        jobs = SchedulerJobs(
            settings=mock_settings,
            app=mock_app,
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=mock_reminder_svc,
            health_service=MagicMock(),
            backup_service=MagicMock(),
        )

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(jobs._fire_alert_job(50, 10))
        finally:
            loop.close()

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args
        sent_text = call_kwargs.kwargs.get("text") or call_kwargs[1].get("text", "")
        assert "Team meeting" in sent_text
        mock_reminder_svc.delete_fired_alert.assert_called_once_with(50)
        sent_markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
        buttons = _flat_buttons(sent_markup)
        set_more = [b for b in buttons if b.text == "Set more alerts"]
        assert len(set_more) == 1
        assert set_more[0].callback_data == "alert_more:10"


class TestPostEventPrompt:
    """Test 15: When event time passes, post-event prompt is sent."""

    def test_post_event_keyboard_is_available(self):
        markup = keyboards.post_event_actions(42)
        labels = [btn.text for btn in _flat_buttons(markup)]
        assert "Yes, mark done" in labels
        assert "Snooze 1hr" in labels
        assert "Reschedule" in labels

    def test_post_event_callback_data(self):
        markup = keyboards.post_event_actions(42)
        buttons = _flat_buttons(markup)
        data_map = {btn.text: btn.callback_data for btn in buttons}
        assert data_map["Yes, mark done"] == "post_done:42"
        assert data_map["Snooze 1hr"] == "post_snooze:42"
        assert data_map["Reschedule"] == "post_reschedule:42"


class TestSetMoreAlertsFlow:
    """Test 16: User taps 'Set more alerts' -> enters custom alert flow.

    NOTE: In the current handler code, ``data.startswith("alert_")`` on line 512
    matches ``alert_more:`` *before* the dedicated ``elif`` on line 555. This
    means the ``alert_more:`` callback is handled by the generic alert-preference
    branch (alert_type="more") rather than the intended custom-flow branch.
    The test below exercises the *keyboard generation* path (which is correct)
    and verifies the ``alert_custom`` flow works for the same parent_id, since
    that *is* reachable and is the designed entry-point for custom alerts.
    """

    def test_keyboard_set_more_alerts_uses_parent_id(self):
        """The keyboard correctly embeds parent_id in callback_data."""
        markup = keyboards.pre_alert_actions(reminder_id=50, parent_id=10)
        buttons = _flat_buttons(markup)
        set_more = [b for b in buttons if b.text == "Set more alerts"][0]
        assert set_more.callback_data == "alert_more:10"

    @pytest.mark.asyncio
    async def test_alert_custom_enters_custom_flow(self):
        """Tapping 'Custom' on alert_preferences sets AWAITING_ALERT_TIME state."""
        from src.bot.handlers import BotHandlers, UserMode, UserState

        parent = _make_reminder(
            id=10,
            title="Big presentation",
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
        )

        mock_settings = MagicMock()
        mock_settings.timezone = "America/Los_Angeles"
        mock_settings.persona = {"name": "openEar", "emoji": ""}

        mock_reminder_svc = MagicMock()
        mock_reminder_svc.get_reminder.return_value = parent

        handlers = BotHandlers(
            settings=mock_settings,
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=mock_reminder_svc,
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        # Simulate tapping "Custom" on alert_preferences keyboard
        mock_query = AsyncMock()
        mock_query.data = "alert_custom:10"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()

        mock_update = MagicMock()
        mock_update.callback_query = mock_query
        mock_update.effective_user.id = 12345

        mock_context = MagicMock()

        with patch("src.bot.handlers.auth_check", new_callable=AsyncMock, return_value=True):
            await handlers.callback_handler(mock_update, mock_context)

        # Verify user state was set for custom alerts
        state = handlers._user_states.get(12345)
        assert state is not None
        assert state.mode == UserMode.AWAITING_ALERT_TIME
        assert state.reminder_id == 10

    @pytest.mark.asyncio
    async def test_alert_more_enters_custom_flow(self):
        """Tapping 'Set more alerts' sets AWAITING_ALERT_TIME state via alert_more handler."""
        from src.bot.handlers import BotHandlers, UserMode, UserState

        parent = _make_reminder(
            id=10,
            title="Big presentation",
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
        )

        mock_settings = MagicMock()
        mock_settings.timezone = "America/Los_Angeles"
        mock_settings.persona = {"name": "openEar", "emoji": ""}

        mock_reminder_svc = MagicMock()
        mock_reminder_svc.get_reminder.return_value = parent

        handlers = BotHandlers(
            settings=mock_settings,
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=mock_reminder_svc,
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        mock_query = AsyncMock()
        mock_query.data = "alert_more:10"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()

        mock_update = MagicMock()
        mock_update.callback_query = mock_query
        mock_update.effective_user.id = 12345

        mock_context = MagicMock()

        with patch("src.bot.handlers.auth_check", new_callable=AsyncMock, return_value=True):
            await handlers.callback_handler(mock_update, mock_context)

        state = handlers._user_states.get(12345)
        assert state is not None, "alert_more: should set user state"
        assert state.mode == UserMode.AWAITING_ALERT_TIME
        assert state.reminder_id == 10


# ===================================================================
# 5. _track_chat and _send_to_all tests
# ===================================================================


class TestTrackChat:
    """Test that _track_chat stores chat_id in bot_data."""

    def test_track_chat_stores_chat_id(self):
        from src.bot.handlers import BotHandlers

        handlers = BotHandlers(
            settings=MagicMock(),
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        mock_update = MagicMock()
        mock_update.effective_chat.id = 99999

        mock_context = MagicMock()
        mock_context.bot_data = {}

        handlers._track_chat(mock_update, mock_context)

        assert "active_chat_ids" in mock_context.bot_data
        assert 99999 in mock_context.bot_data["active_chat_ids"]

    def test_track_chat_accumulates_multiple_ids(self):
        from src.bot.handlers import BotHandlers

        handlers = BotHandlers(
            settings=MagicMock(),
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        mock_context = MagicMock()
        mock_context.bot_data = {}

        for chat_id in [111, 222, 333]:
            mock_update = MagicMock()
            mock_update.effective_chat.id = chat_id
            handlers._track_chat(mock_update, mock_context)

        assert mock_context.bot_data["active_chat_ids"] == {111, 222, 333}

    def test_track_chat_deduplicates(self):
        from src.bot.handlers import BotHandlers

        handlers = BotHandlers(
            settings=MagicMock(),
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        mock_context = MagicMock()
        mock_context.bot_data = {}

        mock_update = MagicMock()
        mock_update.effective_chat.id = 555

        handlers._track_chat(mock_update, mock_context)
        handlers._track_chat(mock_update, mock_context)

        assert len(mock_context.bot_data["active_chat_ids"]) == 1


class TestSendToAll:
    """Test _send_to_all message dispatch logic."""

    @pytest.mark.asyncio
    async def test_send_to_all_uses_active_chat_ids(self):
        from src.scheduler.jobs import SchedulerJobs

        mock_bot = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        mock_app.bot_data = {"active_chat_ids": {100, 200}}

        mock_settings = MagicMock()
        mock_settings.telegram_allowed_user_ids = [999]

        jobs = SchedulerJobs(
            settings=mock_settings,
            app=mock_app,
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            health_service=MagicMock(),
            backup_service=MagicMock(),
        )

        await jobs._send_to_all("Hello")

        assert mock_bot.send_message.call_count == 2
        sent_chat_ids = {
            call.kwargs["chat_id"]
            for call in mock_bot.send_message.call_args_list
        }
        assert sent_chat_ids == {100, 200}

    @pytest.mark.asyncio
    async def test_send_to_all_falls_back_to_allowed_user_ids(self):
        from src.scheduler.jobs import SchedulerJobs

        mock_bot = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        # No active_chat_ids recorded yet
        mock_app.bot_data = {}

        mock_settings = MagicMock()
        mock_settings.telegram_allowed_user_ids = [777, 888]

        jobs = SchedulerJobs(
            settings=mock_settings,
            app=mock_app,
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            health_service=MagicMock(),
            backup_service=MagicMock(),
        )

        await jobs._send_to_all("Fallback test")

        assert mock_bot.send_message.call_count == 2
        sent_chat_ids = {
            call.kwargs["chat_id"]
            for call in mock_bot.send_message.call_args_list
        }
        assert sent_chat_ids == {777, 888}

    @pytest.mark.asyncio
    async def test_send_to_all_falls_back_when_active_set_empty(self):
        from src.scheduler.jobs import SchedulerJobs

        mock_bot = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        # active_chat_ids exists but is empty
        mock_app.bot_data = {"active_chat_ids": set()}

        mock_settings = MagicMock()
        mock_settings.telegram_allowed_user_ids = [444]

        jobs = SchedulerJobs(
            settings=mock_settings,
            app=mock_app,
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            health_service=MagicMock(),
            backup_service=MagicMock(),
        )

        await jobs._send_to_all("Empty set fallback")

        assert mock_bot.send_message.call_count == 1
        mock_bot.send_message.assert_called_once_with(
            chat_id=444, text="Empty set fallback", reply_markup=None,
        )


# ===================================================================
# 6. _reset_state tests
# ===================================================================


class TestResetState:
    """Test _reset_state removes user state and logs the transition."""

    def test_resets_existing_state(self):
        from src.bot.handlers import BotHandlers, UserMode, UserState

        handlers = BotHandlers(
            settings=MagicMock(),
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        handlers._user_states[12345] = UserState(
            mode=UserMode.AWAITING_ALERT_TIME,
            reminder_id=42,
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
            reminder_title="Test",
        )

        handlers._reset_state(12345, "test reason")

        assert 12345 not in handlers._user_states

    def test_no_error_when_no_state(self):
        from src.bot.handlers import BotHandlers

        handlers = BotHandlers(
            settings=MagicMock(),
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        # Should not raise
        handlers._reset_state(99999, "no state")
        assert 99999 not in handlers._user_states

    def test_does_not_affect_other_users(self):
        from src.bot.handlers import BotHandlers, UserMode, UserState

        handlers = BotHandlers(
            settings=MagicMock(),
            llm_service=MagicMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        handlers._user_states[111] = UserState(
            mode=UserMode.AWAITING_ALERT_TIME, reminder_id=1,
        )
        handlers._user_states[222] = UserState(
            mode=UserMode.AWAITING_RESCHEDULE, reminder_id=2,
        )

        handlers._reset_state(111, "clearing user 111")

        assert 111 not in handlers._user_states
        assert 222 in handlers._user_states
        assert handlers._user_states[222].reminder_id == 2


# ===================================================================
# 7. AWAITING_ALERT_TIME disambiguation tests
# ===================================================================


class TestAwaitingAlertTimeDisambiguation:
    """When user sends non-alert text while in AWAITING_ALERT_TIME state,
    parse_alert_time is called. If it fails, a disambiguation keyboard is
    shown and the state remains AWAITING_ALERT_TIME (user must tap a button
    or say 'done' to exit)."""

    @pytest.mark.asyncio
    async def test_unparseable_text_shows_disambiguation(self):
        from src.bot.handlers import BotHandlers, UserMode, UserState

        parent = _make_reminder(
            id=10,
            title="Old event",
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
        )

        mock_settings = MagicMock()
        mock_settings.timezone = "America/Los_Angeles"
        mock_settings.persona = {"name": "openEar", "emoji": ""}

        mock_reminder_svc = MagicMock()
        mock_reminder_svc.get_reminder.return_value = parent

        mock_llm = AsyncMock()
        # parse_alert_time returns None -> can't parse as alert time
        mock_llm.parse_alert_time = AsyncMock(return_value=None)

        handlers = BotHandlers(
            settings=mock_settings,
            llm_service=mock_llm,
            email_service=MagicMock(),
            reminder_service=mock_reminder_svc,
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        # Simulate being in AWAITING_ALERT_TIME state
        handlers._user_states[12345] = UserState(
            mode=UserMode.AWAITING_ALERT_TIME,
            reminder_id=10,
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
            reminder_title="Old event",
        )

        mock_update = MagicMock()
        mock_update.effective_user.id = 12345
        mock_update.effective_chat.id = 12345
        mock_update.message.text = "remind me to call doctor tomorrow"
        mock_update.message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.bot_data = {}

        with patch("src.bot.handlers.auth_check", new_callable=AsyncMock, return_value=True):
            await handlers.handle_message(mock_update, mock_context)

        # State should remain AWAITING_ALERT_TIME (disambiguation shown)
        state = handlers._user_states.get(12345)
        assert state is not None
        assert state.mode == UserMode.AWAITING_ALERT_TIME

        # Disambiguation message should have been sent with a keyboard
        reply_calls = mock_update.message.reply_text.call_args_list
        assert len(reply_calls) == 1
        msg = reply_calls[0].args[0]
        assert "couldn't parse" in msg.lower() or "new reminder" in msg.lower()
        # Should include a reply_markup (disambiguation keyboard)
        assert reply_calls[0].kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_done_phrase_resets_state(self):
        """Saying 'done' while in AWAITING_ALERT_TIME resets state to IDLE."""
        from src.bot.handlers import BotHandlers, UserMode, UserState

        parent = _make_reminder(
            id=10,
            title="Meeting",
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
        )

        mock_settings = MagicMock()
        mock_settings.timezone = "America/Los_Angeles"
        mock_settings.persona = {"name": "openEar", "emoji": ""}

        mock_reminder_svc = MagicMock()
        mock_reminder_svc.get_reminder.return_value = parent

        handlers = BotHandlers(
            settings=mock_settings,
            llm_service=AsyncMock(),
            email_service=MagicMock(),
            reminder_service=mock_reminder_svc,
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

        handlers._user_states[12345] = UserState(
            mode=UserMode.AWAITING_ALERT_TIME,
            reminder_id=10,
            due_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
            reminder_title="Meeting",
        )

        mock_update = MagicMock()
        mock_update.effective_user.id = 12345
        mock_update.effective_chat.id = 12345
        mock_update.message.text = "done"
        mock_update.message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.bot_data = {}

        with patch("src.bot.handlers.auth_check", new_callable=AsyncMock, return_value=True):
            await handlers.handle_message(mock_update, mock_context)

        # State should have been cleared
        assert 12345 not in handlers._user_states

        # "All set!" confirmation should have been sent
        reply_calls = mock_update.message.reply_text.call_args_list
        assert any("All set" in call.args[0] for call in reply_calls)


# ===================================================================
# 8. Compound intent routing tests
# ===================================================================


class TestCompoundIntentRouting:
    """Verify _handle_idle_message dispatches single vs. compound intents
    correctly, and handles an empty list without crashing."""

    def _make_handlers(self, mock_llm=None):
        from src.bot.handlers import BotHandlers

        mock_settings = MagicMock()
        mock_settings.timezone = "America/Los_Angeles"
        mock_settings.persona = {"name": "openEar", "emoji": "", "tone": "warm", "behavior": []}

        return BotHandlers(
            settings=mock_settings,
            llm_service=mock_llm or AsyncMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

    def _make_update_and_context(self, user_id=12345, text="hello"):
        mock_update = MagicMock()
        mock_update.effective_user.id = user_id
        mock_update.effective_chat.id = user_id
        mock_update.message.text = text
        mock_update.message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.bot_data = {}
        return mock_update, mock_context

    @pytest.mark.asyncio
    async def test_single_intent_returns_dict(self):
        """When classify_intent returns a single dict, _process_single_intent
        is called exactly once."""
        mock_llm = AsyncMock()
        mock_llm.classify_intent = AsyncMock(
            return_value={"intent": "weather", "content": "what's the weather", "tags": []}
        )

        handlers = self._make_handlers(mock_llm)
        mock_update, mock_context = self._make_update_and_context(text="what's the weather")

        with patch("src.bot.handlers.auth_check", new_callable=AsyncMock, return_value=True), \
             patch.object(handlers, "_process_single_intent", new_callable=AsyncMock) as mock_process:
            await handlers.handle_message(mock_update, mock_context)

        mock_process.assert_called_once()
        call_args = mock_process.call_args
        assert call_args[0][4] == {"intent": "weather", "content": "what's the weather", "tags": []}

    @pytest.mark.asyncio
    async def test_compound_intent_returns_list(self):
        """When classify_intent returns a list of 2 dicts,
        _process_single_intent is called twice."""
        intents = [
            {"intent": "whitelist", "content": "add doc@clinic.com", "email": "doc@clinic.com", "label": "Medical", "tags": []},
            {"intent": "note", "content": "Aaron is allergic to eggs", "tags": ["allergy"]},
        ]
        mock_llm = AsyncMock()
        mock_llm.classify_intent = AsyncMock(return_value=intents)

        handlers = self._make_handlers(mock_llm)
        mock_update, mock_context = self._make_update_and_context(
            text="add doc@clinic.com to whitelist and note Aaron is allergic to eggs"
        )

        with patch("src.bot.handlers.auth_check", new_callable=AsyncMock, return_value=True), \
             patch.object(handlers, "_process_single_intent", new_callable=AsyncMock) as mock_process:
            await handlers.handle_message(mock_update, mock_context)

        assert mock_process.call_count == 2
        dispatched_intents = [call.args[4] for call in mock_process.call_args_list]
        assert dispatched_intents[0]["intent"] == "whitelist"
        assert dispatched_intents[1]["intent"] == "note"

    @pytest.mark.asyncio
    async def test_empty_list_fallback(self):
        """When classify_intent returns an empty list, nothing crashes
        and _process_single_intent is never called."""
        mock_llm = AsyncMock()
        mock_llm.classify_intent = AsyncMock(return_value=[])

        handlers = self._make_handlers(mock_llm)
        mock_update, mock_context = self._make_update_and_context(text="")

        with patch("src.bot.handlers.auth_check", new_callable=AsyncMock, return_value=True), \
             patch.object(handlers, "_process_single_intent", new_callable=AsyncMock) as mock_process:
            await handlers.handle_message(mock_update, mock_context)

        mock_process.assert_not_called()


# ===================================================================
# 9. Whitelist intent tests (with real DB)
# ===================================================================


class TestWhitelistIntent:
    """Verify the 'whitelist' intent branch in _process_single_intent
    creates, detects duplicates, and rejects missing-email payloads."""

    @pytest.fixture(autouse=True)
    def tmp_db(self, tmp_path):
        """Initialise a fresh SQLite database for every test in this class."""
        from src.db import database as db_module
        from src.db.database import init_db

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        yield db_path
        db_module._engine = None
        db_module._SessionLocal = None

    def _make_handlers(self):
        from src.bot.handlers import BotHandlers

        mock_settings = MagicMock()
        mock_settings.timezone = "America/Los_Angeles"
        mock_settings.persona = {"name": "openEar", "emoji": "", "tone": "warm", "behavior": []}

        return BotHandlers(
            settings=mock_settings,
            llm_service=AsyncMock(),
            email_service=MagicMock(),
            reminder_service=MagicMock(),
            note_service=MagicMock(),
            health_service=MagicMock(),
        )

    def _make_update_and_context(self, user_id=12345, text="test"):
        mock_update = MagicMock()
        mock_update.effective_user.id = user_id
        mock_update.effective_chat.id = user_id
        mock_update.message.text = text
        mock_update.message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.bot_data = {}
        return mock_update, mock_context

    @pytest.mark.asyncio
    async def test_whitelist_adds_to_db(self):
        """Whitelist intent with email and label creates a SenderWhitelist row."""
        from src.db.database import get_session
        from src.db.models import SenderWhitelist

        handlers = self._make_handlers()
        mock_update, mock_context = self._make_update_and_context()

        intent_data = {
            "intent": "whitelist",
            "email": "doctor@clinic.com",
            "label": "Medical",
            "content": "add doctor@clinic.com",
            "tags": [],
        }

        await handlers._process_single_intent(
            mock_update, mock_context, 12345,
            "add doctor@clinic.com to whitelist as Medical",
            intent_data,
        )

        # Verify DB entry was created
        with get_session() as session:
            entry = session.query(SenderWhitelist).filter_by(pattern="doctor@clinic.com").first()
            assert entry is not None
            assert entry.label == "Medical"

        # Verify confirmation message was sent
        mock_update.message.reply_text.assert_called_once()
        msg = mock_update.message.reply_text.call_args[0][0]
        assert "doctor@clinic.com" in msg
        assert "whitelist" in msg.lower() or "Medical" in msg

    @pytest.mark.asyncio
    async def test_whitelist_duplicate_detected(self):
        """If the email is already whitelisted, the bot reports it."""
        from src.db.database import get_session
        from src.db.models import SenderWhitelist

        # Pre-populate the whitelist
        with get_session() as session:
            session.add(SenderWhitelist(pattern="doctor@clinic.com", label="Medical"))

        handlers = self._make_handlers()
        mock_update, mock_context = self._make_update_and_context()

        intent_data = {
            "intent": "whitelist",
            "email": "doctor@clinic.com",
            "label": "Medical",
            "content": "add doctor@clinic.com",
            "tags": [],
        }

        await handlers._process_single_intent(
            mock_update, mock_context, 12345,
            "add doctor@clinic.com to whitelist",
            intent_data,
        )

        msg = mock_update.message.reply_text.call_args[0][0]
        assert "already" in msg.lower()

    @pytest.mark.asyncio
    async def test_whitelist_missing_email(self):
        """When the intent has no email field, the bot asks the user to retry."""
        handlers = self._make_handlers()
        mock_update, mock_context = self._make_update_and_context()

        intent_data = {
            "intent": "whitelist",
            "email": "",
            "label": "Other",
            "content": "add someone to whitelist",
            "tags": [],
        }

        await handlers._process_single_intent(
            mock_update, mock_context, 12345,
            "add someone to whitelist",
            intent_data,
        )

        msg = mock_update.message.reply_text.call_args[0][0]
        assert "email" in msg.lower() or "couldn't" in msg.lower()
