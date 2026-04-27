"""Telegram bot handlers for openEar.

Every handler checks auth first. Messages from unauthorized users
are silently dropped.

Conversation context management:
- Keeps the last 20 messages as active context per user (N5)
- When message 21 arrives, messages 1-10 are summarized by the LLM
- Summary is stored as a context_summary role entry
- User is notified when summarization occurs

N1: This file is implemented across Steps 3D, 3E, and 3F.
N4: Uses normal import for Reminder model (no __import__ hack).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.auth import auth_check
from src.bot import formatters, keyboards
from src.config import Settings
from src.db.database import get_session
from src.db.models import Conversation, Reminder, UserConfig  # N4: normal import
from src.services.email_service import EmailService, EmailServiceUnavailable
from src.services.health_service import HealthService
from src.services.info_service import get_stock_quote, get_weather, search_news
from src.services.llm_service import LLMService
from src.services.note_service import NoteService
from src.services.reminder_service import ReminderService

logger = logging.getLogger(__name__)

# Maximum active messages in conversation context before summarization
MAX_ACTIVE_MESSAGES = 20
SUMMARIZE_BATCH_SIZE = 10

# Human-readable labels for setup setting keys
_SETTING_LABELS = {
    "email_check_morning": "Morning email check",
    "email_check_evening": "Evening email check",
    "stock_symbols": "Stock watchlist",
    "quiet_start": "Quiet hours start",
    "quiet_end": "Quiet hours end",
}


class UserMode(Enum):
    IDLE = auto()
    AWAITING_ALERT_TIME = auto()
    AWAITING_RESCHEDULE = auto()


@dataclass
class UserState:
    """Per-user conversation state."""
    mode: UserMode = UserMode.IDLE
    reminder_id: int | None = None      # which reminder we are operating on
    due_at: datetime | None = None       # event time (for alert computation)
    reminder_title: str | None = None    # cached for display without DB lookup


class BotHandlers:
    """Registers and implements all Telegram bot handlers."""

    def __init__(
        self,
        settings: Settings,
        llm_service: LLMService,
        email_service: EmailService,
        reminder_service: ReminderService,
        note_service: NoteService,
        health_service: HealthService,
    ) -> None:
        self.settings = settings
        self.llm = llm_service
        self.email = email_service
        self.reminders = reminder_service
        self.notes = note_service
        self.health = health_service
        # TODO: Add TTL expiration for stale states (intentionally deferred)
        self._user_states: dict[int, UserState] = {}

    def _create_pre_alerts(self, parent_reminder, pre_alerts, due_at, context=None):
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        if not pre_alerts or not isinstance(pre_alerts, list):
            return

        local_tz = ZoneInfo(self.settings.timezone)
        due_local = due_at.astimezone(local_tz)

        for alert in pre_alerts:
            offset = alert.get("offset", "")
            alert_time = alert.get("time", "08:00")
            label = alert.get("label", "Reminder")
            h, m = int(alert_time.split(":")[0]), int(alert_time.split(":")[1])

            if offset == "1d_before":
                alert_dt = (due_local - timedelta(days=1)).replace(hour=h, minute=m, second=0)
            elif offset == "2d_before":
                alert_dt = (due_local - timedelta(days=2)).replace(hour=h, minute=m, second=0)
            elif offset == "morning_of":
                alert_dt = due_local.replace(hour=h, minute=m, second=0)
            elif offset == "1h_before":
                alert_dt = due_local - timedelta(hours=1)
            else:
                continue

            alert_utc = alert_dt.astimezone(timezone.utc)
            parent_title = parent_reminder.title or "Reminder"
            if alert_utc > datetime.now(timezone.utc):
                alert_record = self.reminders.create_reminder(
                    title=f"{label}: {parent_title}",
                    due_at=alert_utc,
                    source="pre_alert",
                    source_ref=str(parent_reminder.id),
                    alert_label=label,
                )
                # Schedule a DateTrigger job for exact-time firing
                self._schedule_alert_job(alert_record, parent_reminder, context)

    def _schedule_alert_job(self, alert_record, parent_reminder, context=None):
        """Schedule a DateTrigger job for the given alert via SchedulerJobs.

        Retrieves the scheduler_jobs instance from bot_data. If unavailable
        (e.g., during tests), logs a warning and skips scheduling.
        """
        scheduler_jobs = None
        if context is not None:
            scheduler_jobs = context.bot_data.get("scheduler_jobs")
        if scheduler_jobs is None:
            logger.warning(
                "scheduler_jobs not available; alert #%d will rely on polling",
                alert_record.id,
            )
            return
        scheduler_jobs.schedule_alert(alert_record, parent_reminder)

    def _cancel_alert_job(self, alert_id, context=None):
        """Cancel a scheduled DateTrigger job for the given alert ID.

        Retrieves the scheduler_jobs instance from bot_data. Silently
        skips if unavailable.
        """
        scheduler_jobs = None
        if context is not None:
            scheduler_jobs = context.bot_data.get("scheduler_jobs")
        if scheduler_jobs is None:
            logger.debug(
                "scheduler_jobs not available; cannot cancel alert #%d job",
                alert_id,
            )
            return
        scheduler_jobs.cancel_alert(alert_id)

    def _format_alert_summary(self, pre_alerts, due_at=None):
        if not pre_alerts or not isinstance(pre_alerts, list):
            return "At the time only"
        parts = []
        for a in pre_alerts:
            label = a.get("label", a.get("offset", "Alert"))
            time = a.get("time", "")
            if time:
                parts.append(f"{label} ({time})")
            else:
                parts.append(label)
        return " + ".join(parts)

    def _get_state(self, user_id: int) -> UserState:
        """Get or create the per-user conversation state."""
        return self._user_states.setdefault(user_id, UserState())

    def _reset_state(self, user_id: int, reason: str) -> None:
        """Reset user state to IDLE and log the transition."""
        old = self._user_states.pop(user_id, None)
        if old and old.mode != UserMode.IDLE:
            logger.info(
                "Reset state for user %d (reason: %s): was %s, reminder_id=%s",
                user_id, reason, old.mode.name, old.reminder_id,
            )

    def get_handlers(self) -> list:
        """Return all handler objects to register with the Application."""
        return [
            CommandHandler("start", self.cmd_start),
            CommandHandler("help", self.cmd_help),
            CommandHandler("status", self.cmd_status),
            CommandHandler("briefing", self.cmd_briefing),
            CommandHandler("reminders", self.cmd_list_reminders),
            CommandHandler("notes", self.cmd_list_notes),
            CommandHandler("note", self.cmd_save_note),
            CommandHandler("whitelist", self.cmd_whitelist),
            CallbackQueryHandler(self.callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message),
        ]

    # ---- Command Handlers (Step 3D) ----

    def _track_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Record the chat_id so scheduled messages reach this chat.

        Captures both DM and group chat IDs. This is called at the top
        of every handler so that scheduler jobs (reminders, briefings,
        heartbeats) are delivered to every chat the bot is active in.
        """
        chat_id = update.effective_chat.id
        context.bot_data.setdefault("active_chat_ids", set()).add(chat_id)

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)
        name = self.settings.persona.get("name", "openEar")
        emoji = self.settings.persona.get("emoji", "")
        await update.message.reply_text(
            f"Hi! I'm {name} {emoji}, your personal assistant.\n\n"
            "I can help with:\n"
            "- Email briefings (/briefing)\n"
            "- Reminders (/reminders)\n"
            "- Notes (/note <text>)\n"
            "- Weather, stocks, news (just ask)\n"
            "- General conversation\n\n"
            "Type /help for more details."
        )

    async def cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)
        await update.message.reply_text(
            "Commands:\n"
            "/start - Introduction\n"
            "/briefing - Check emails now\n"
            "/reminders - List active reminders\n"
            "/notes - List saved notes\n"
            "/note <text> - Save a note\n"
            "/status - System health\n"
            "/help - This message\n\n"
            "Or just talk to me naturally!"
        )

    async def cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)

        groq_status = (
            "CIRCUIT_OPEN"
            if self.llm.circuit_breaker.is_open
            else "OK"
        )
        gmail_status = (
            f"DISCONNECTED -- {self.email.pause_reason}"
            if self.email.is_paused
            else "Connected"
        )
        last_email = (
            self.email.last_check.strftime("%Y-%m-%d %H:%M UTC")
            if self.email.last_check
            else "Never"
        )
        last_groq = (
            datetime.fromtimestamp(
                self.llm.last_successful_call, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            if self.llm.last_successful_call
            else "Never"
        )
        circuit_state = "OPEN" if self.llm.circuit_breaker.is_open else "CLOSED"

        status = self.health.format_status(
            groq_status=groq_status,
            groq_429_count=self.llm.rate_limit_count_24h,
            gmail_status=gmail_status,
            last_email_check=last_email,
            last_groq_call=last_groq,
            circuit_breaker_state=circuit_state,
        )
        await update.message.reply_text(status)

    async def cmd_briefing(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)
        await update.message.reply_text("Checking emails...")
        try:
            emails = await self.email.process_emails()
            text = formatters.format_briefing(emails, self.settings.timezone)
            await update.message.reply_text(text)

            # Send action keyboards for each important email
            for i, email in enumerate(emails):
                if email.get("summary"):
                    await update.message.reply_text(
                        f"Actions for: {email['subject']}",
                        reply_markup=keyboards.briefing_actions(i),
                    )
        except EmailServiceUnavailable as e:
            await update.message.reply_text(
                f"Email service is currently unavailable: {e}\n"
                "Please re-authenticate via scripts/reauth_gmail.py or "
                "scripts/setup_gmail.py."
            )

    async def cmd_list_reminders(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)

        # Only show future reminders or recently notified ones
        parent_reminders = self.reminders.get_future_reminders()

        if not parent_reminders:
            await update.message.reply_text("No active reminders.")
            return

        cards = ["\U0001f4cb Your reminders:\n"]
        reminder_meta = []  # (reminder, has_alerts) for keyboard buttons

        for i, r in enumerate(parent_reminders, 1):
            alerts = self.reminders.get_alerts_for_reminder(r.id)
            active_alerts = [a for a in alerts if a.status in ("active", "notified")]
            card = formatters.format_reminder_card(r, active_alerts, self.settings.timezone)
            cards.append(f"{i}. {card}\n")
            reminder_meta.append((r, len(active_alerts) > 0))

        text = "\n".join(cards)
        await update.message.reply_text(text)

        # Send inline keyboard for each reminder
        for r, has_alerts in reminder_meta:
            await update.message.reply_text(
                f"{r.title}",
                reply_markup=keyboards.reminder_list_actions(r.id, has_alerts),
            )

    async def cmd_list_notes(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)
        notes = self.notes.get_all_notes()
        text = formatters.format_note_list(notes, self.settings.timezone)
        await update.message.reply_text(text)

    async def cmd_whitelist(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)

        args = update.message.text.split(maxsplit=1)
        sub_cmd = args[1].strip().lower() if len(args) > 1 else ""

        from src.db.models import SenderWhitelist

        if sub_cmd.startswith("add "):
            parts = sub_cmd[4:].strip()
            if " as " in parts:
                email, label = parts.rsplit(" as ", 1)
                email = email.strip()
                label = label.strip().title()
            else:
                email = parts
                label = "Other"
            with get_session() as s:
                existing = s.query(SenderWhitelist).filter_by(pattern=email).first()
                if existing:
                    await update.message.reply_text(f"'{email}' is already in the whitelist as '{existing.label}' 🐰")
                else:
                    s.add(SenderWhitelist(pattern=email, label=label))
                    await update.message.reply_text(f"✅ Added '{email}' as '{label}' 🐰")

        elif sub_cmd.startswith("remove "):
            email = sub_cmd[7:].strip()
            with get_session() as s:
                entry = s.query(SenderWhitelist).filter_by(pattern=email).first()
                if entry:
                    s.delete(entry)
                    await update.message.reply_text(f"🗑 Removed '{email}' from whitelist 🐰")
                else:
                    await update.message.reply_text(f"'{email}' not found in whitelist 🐰")

        else:
            with get_session() as s:
                entries = s.query(SenderWhitelist).all()
                for e in entries:
                    s.expunge(e)
            if not entries:
                await update.message.reply_text("Whitelist is empty. Use /whitelist add email@example.com as Label 🐰")
                return
            lines = ["📧 Email whitelist:\n"]
            for e in entries:
                lines.append(f"  • {e.pattern} → {e.label}")
            lines.append(f"\n📝 {len(entries)} entries")
            lines.append("\nCommands:")
            lines.append("/whitelist add email@x.com as Label")
            lines.append("/whitelist remove email@x.com")
            await update.message.reply_text("\n".join(lines))

    async def cmd_save_note(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)
        text = update.message.text
        # Strip the /note prefix
        content = text[len("/note") :].strip()
        if not content:
            await update.message.reply_text("Usage: /note <your note text>")
            return

        # Use LLM to extract tags
        intent = await self.llm.classify_intent(content)
        tags = intent.get("tags", [])

        note = self.notes.save_note(content, tags)
        reply = formatters.format_note(note, self.settings.timezone)

        # Check for recurring pattern to suggest reminder
        has_recurring = any(
            word in content.lower()
            for word in ["every", "weekly", "daily", "monthly", "each"]
        )
        markup = keyboards.note_followup(note.id) if has_recurring else None
        await update.message.reply_text(
            f"Saved! {reply}", reply_markup=markup
        )

    # ---- Message Handler (general conversation + intent routing) (Step 3E) ----

    # Explicit exit phrases -- the user saying "I'm done configuring alerts."
    _DONE_PHRASES = frozenset({
        "done", "no", "nope", "that's it", "that's all",
        "no more", "nothing else", "i'm good", "all set",
    })

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)

        user_message = update.message.text
        user_id = update.effective_user.id
        state = self._get_state(user_id)

        # --- State-based routing (no intent classification) ---

        if state.mode == UserMode.AWAITING_RESCHEDULE:
            await self._handle_reschedule_input(update, user_id, user_message, state)
            return

        if state.mode == UserMode.AWAITING_ALERT_TIME:
            await self._handle_alert_time_input(update, context, user_id, user_message, state)
            return

        # --- IDLE: classify intent normally ---
        await self._handle_idle_message(update, context, user_id, user_message)

    async def _handle_alert_time_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        user_id: int, user_message: str, state: UserState,
    ) -> None:
        """Handle text input while in AWAITING_ALERT_TIME state.

        ALL text goes to parse_alert_time. If parsing fails, we ask the user
        to disambiguate with a keyboard -- never guess from content.
        """
        lower = user_message.lower().strip()

        # Exit phrase: user explicitly says they are done
        if lower in self._DONE_PHRASES:
            self._reset_state(user_id, "user said done")
            await update.message.reply_text("All set!")
            return

        # Verify the reminder still exists
        reminder = self.reminders.get_reminder(state.reminder_id)
        if not reminder:
            self._reset_state(user_id, "reminder no longer exists")
            await update.message.reply_text("That reminder no longer exists.")
            return

        # Send everything to parse_alert_time -- LLM decides if it's a valid time
        alerts = await self.llm.parse_alert_time(
            user_message, state.due_at, self.settings.timezone
        )

        if alerts:
            # Successfully parsed -- create the alert(s)
            created = []
            for alert in alerts:
                alert_utc = alert["datetime"].astimezone(timezone.utc)
                label = alert.get("label", "Custom")
                alert_record = self.reminders.create_reminder(
                    title=f"Alert: {reminder.title}",
                    due_at=alert_utc,
                    source="pre_alert",
                    source_ref=str(reminder.id),
                    alert_label=label,
                )
                # Schedule a DateTrigger job for exact-time firing
                self._schedule_alert_job(alert_record, reminder, context)
                created.append(
                    f"  {label} -- {formatters.to_local(alert_utc, self.settings.timezone)}"
                )

            alert_list = "\n".join(created)
            await update.message.reply_text(
                f"Alert set!\n{alert_list}\n\n"
                f"{formatters.format_reminder(reminder, self.settings.timezone)}\n\n"
                "Want to add more alerts? Reply with a time or say 'done'."
            )
            # Stay in AWAITING_ALERT_TIME
        else:
            # LLM could not parse it as an alert time.
            # Ask the user to disambiguate -- do NOT guess from content.
            await update.message.reply_text(
                "I couldn't parse that as an alert time.\n"
                "Did you mean to set a new reminder instead?",
                reply_markup=keyboards.disambiguate_alert_or_reminder(
                    state.reminder_id
                ),
            )
            # Stay in AWAITING_ALERT_TIME until user taps a button

    async def _handle_reschedule_input(
        self, update: Update, user_id: int, user_message: str, state: UserState,
    ) -> None:
        """Handle text input while in AWAITING_RESCHEDULE state."""
        reminder = self.reminders.get_reminder(state.reminder_id)
        if not reminder:
            self._reset_state(user_id, "reschedule reminder not found")
            await update.message.reply_text("Reminder not found.")
            return

        parsed = await self.llm.parse_reminder_time(user_message, self.settings.timezone)
        if parsed:
            try:
                due_at = datetime.fromisoformat(parsed["due_at"])
                if due_at.tzinfo is None:
                    from zoneinfo import ZoneInfo
                    due_at = due_at.replace(tzinfo=ZoneInfo(self.settings.timezone))
                due_at_utc = due_at.astimezone(timezone.utc)

                with get_session() as session:
                    r = session.get(Reminder, state.reminder_id)
                    if r:
                        r.due_at = due_at_utc
                        r.status = "active"

                self._reset_state(user_id, "reschedule completed")
                time_str = formatters.to_local(due_at_utc, self.settings.timezone)
                await update.message.reply_text(
                    f"Rescheduled '{reminder.title}' to {time_str}."
                )
            except Exception as e:
                logger.error("Failed to reschedule: %s", e)
                await update.message.reply_text(
                    "Sorry, I couldn't understand that time. Try again?"
                )
        else:
            await update.message.reply_text(
                "Sorry, I couldn't understand that time. Try again?"
            )

    async def _handle_idle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        user_id: int, user_message: str,
    ) -> None:
        """Handle text input in IDLE state -- runs intent classification."""
        # Check for email_remind pending state (email index tracking)
        state = self._get_state(user_id)
        if state.reminder_id is not None and state.mode == UserMode.IDLE and state.reminder_title == "__email_remind__":
            # This is the email_remind flow: user is providing a time for an email reminder
            email_idx = state.reminder_id
            parsed = await self.llm.parse_reminder_time(user_message, self.settings.timezone)
            if parsed:
                try:
                    due_at = datetime.fromisoformat(parsed["due_at"])
                    if due_at.tzinfo is None:
                        from zoneinfo import ZoneInfo
                        due_at = due_at.replace(tzinfo=ZoneInfo(self.settings.timezone))
                    due_at_utc = due_at.astimezone(timezone.utc)
                    title = parsed.get("title", f"Follow up on email #{email_idx}")
                    self.reminders.create_reminder(title=title, due_at=due_at_utc)
                    self._reset_state(user_id, "email remind completed")
                    time_str = formatters.to_local(due_at_utc, self.settings.timezone)
                    await update.message.reply_text(
                        f"Reminder set for {time_str}."
                    )
                except Exception as e:
                    logger.error("Failed to create email reminder: %s", e)
                    self._reset_state(user_id, "email remind failed")
                    await update.message.reply_text(
                        "Sorry, I couldn't understand that time. Try again?"
                    )
            else:
                await update.message.reply_text(
                    "Sorry, I couldn't understand that time. Try again?"
                )
            return

        # Classify intent (may return list for compound messages)
        intent_data = await self.llm.classify_intent(user_message)

        if isinstance(intent_data, list):
            for sub_intent in intent_data:
                try:
                    await self._process_single_intent(update, context, user_id, user_message, sub_intent)
                except Exception as e:
                    logger.error("Sub-intent failed: %s (intent: %s)", e, sub_intent.get("intent"))
            return

        await self._process_single_intent(update, context, user_id, user_message, intent_data)

    async def _process_single_intent(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        user_id: int, user_message: str, intent_data: dict,
    ) -> None:
        """Process a single classified intent."""
        intent = intent_data.get("intent", "general")

        if intent == "weather":
            result = await get_weather()
            await update.message.reply_text(result)
        elif intent == "stock":
            content = intent_data.get("content", user_message)
            words = content.upper().split()
            symbol = next(
                (w for w in words if w.isalpha() and len(w) <= 5),
                content.split()[-1] if content.split() else "SPY",
            )
            import asyncio as _aio
            quote_task = get_stock_quote(symbol)
            news_task = _aio.wait_for(
                search_news(f"{symbol} stock price today", max_results=3),
                timeout=8.0,
            )
            quote = await quote_task
            try:
                news = await news_task
                result = f"{quote}\n\n Why it moved:\n{news}"
            except (TimeoutError, Exception):
                result = quote
            await update.message.reply_text(result)
        elif intent == "news":
            content = intent_data.get("content", user_message)
            result = await search_news(content)
            await update.message.reply_text(result)
        elif intent == "note":
            content = intent_data.get("content", user_message)
            tags = intent_data.get("tags", [])
            note = self.notes.save_note(content, tags)
            reply = formatters.format_note(note, self.settings.timezone)
            await update.message.reply_text(f"Saved! {reply}")
        elif intent == "reminder":
            parsed = await self.llm.parse_reminder_time(user_message, self.settings.timezone)
            if parsed:
                try:
                    due_at = datetime.fromisoformat(parsed["due_at"])
                    if due_at.tzinfo is None:
                        from zoneinfo import ZoneInfo
                        due_at = due_at.replace(tzinfo=ZoneInfo(self.settings.timezone))
                    due_at_utc = due_at.astimezone(timezone.utc)
                    reminder = self.reminders.create_reminder(
                        title=parsed["title"],
                        due_at=due_at_utc,
                        recurrence=parsed.get("recurrence"),
                    )

                    pre_alerts = parsed.get("pre_alerts", "ask")

                    if pre_alerts == "ask":
                        self._reset_state(user_id, "new reminder created (ask alerts)")
                        self._user_states[user_id] = UserState(
                            mode=UserMode.IDLE,
                            reminder_id=reminder.id,
                            due_at=due_at_utc,
                            reminder_title=parsed["title"],
                        )
                        text = formatters.format_reminder(reminder, self.settings.timezone)
                        await update.message.reply_text(
                            f"Reminder set! {text}\n\n"
                            "How would you like to be alerted?",
                            reply_markup=keyboards.alert_preferences(reminder.id),
                        )
                    else:
                        self._create_pre_alerts(reminder, pre_alerts, due_at, context)
                        text = formatters.format_reminder(reminder, self.settings.timezone)
                        alert_desc = self._format_alert_summary(pre_alerts)
                        await update.message.reply_text(
                            f"Reminder set! {text}\n\n"
                            f"Alerts: {alert_desc}"
                        )
                except Exception as e:
                    logger.error("Failed to create reminder: %s", e)
                    await update.message.reply_text(
                        "Sorry, I couldn't parse that reminder. "
                        "Try something like 'remind me to call doctor tomorrow at 3pm'"
                    )
            else:
                await update.message.reply_text(
                    "I couldn't understand that reminder. Could you rephrase?"
                )
        elif intent == "modify":
            await self._handle_modify_reminder(update, context, user_id, user_message, intent_data)
        elif intent == "whitelist":
            email = intent_data.get("email", "")
            label = intent_data.get("label", "Other")
            if email:
                from src.db.models import SenderWhitelist
                with get_session() as s:
                    existing = s.query(SenderWhitelist).filter_by(pattern=email).first()
                    if not existing:
                        s.add(SenderWhitelist(pattern=email, label=label))
                        await update.message.reply_text(f"Added {email} to whitelist as '{label}' 🐰")
                    else:
                        await update.message.reply_text(f"{email} is already in the whitelist 🐰")
            else:
                await update.message.reply_text("Couldn't find the email address. Try: 'add doctor@clinic.com to whitelist as Medical' 🐰")
        elif intent == "setup":
            await self._handle_setup(update, context, user_id, user_message, intent_data)
        else:
            # General conversation with context management
            # N5: pass user_id for per-user context
            history = await self._get_conversation_context(user_id)
            response = await self.llm.chat(
                user_message, history, self.settings.persona
            )
            await update.message.reply_text(response)

            # Store conversation turn (N5: with user_id)
            self._store_conversation("user", user_message, user_id)
            self._store_conversation("assistant", response, user_id)

            # Check if summarization is needed
            await self._maybe_summarize_context(update, user_id)

    # ---- Modify Reminder Handler ----

    async def _handle_modify_reminder(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        user_id: int, user_message: str, intent_data: dict,
    ) -> None:
        """Handle natural language reminder modification requests.

        Fetches active reminders, asks the LLM which one the user means
        and what to do, then executes the modification.
        """
        # Get active parent reminders (exclude pre-alerts)
        all_reminders = self.reminders.get_active_reminders()
        parent_reminders = [r for r in all_reminders if r.source != "pre_alert"]

        if not parent_reminders:
            await update.message.reply_text(
                "You don't have any active reminders to modify."
            )
            return

        # Build a list of reminders for the LLM
        reminder_dicts = []
        for r in parent_reminders:
            due_str = formatters.to_local(r.due_at, self.settings.timezone)
            reminder_dicts.append({
                "id": r.id,
                "title": r.title,
                "due_at": due_str,
            })

        # Ask LLM to match the reminder and parse the modification
        modification = await self.llm.parse_modification(
            user_message, reminder_dicts, self.settings.timezone
        )

        if modification is None:
            await update.message.reply_text(
                "Sorry, I couldn't process that modification right now. "
                "Please try again in a moment."
            )
            return

        reminder_id = modification.get("reminder_id")
        action = modification.get("action", "none")

        if reminder_id is None or action == "none":
            # LLM could not match any reminder
            lines = ["I couldn't find a matching reminder. Here are your active reminders:\n"]
            for r in parent_reminders:
                due_str = formatters.to_local(r.due_at, self.settings.timezone)
                lines.append(f"  #{r.id}: {r.title} - {due_str}")
            await update.message.reply_text("\n".join(lines))
            return

        reminder = self.reminders.get_reminder(reminder_id)
        if not reminder:
            await update.message.reply_text(
                f"Reminder #{reminder_id} not found. It may have been completed or deleted."
            )
            return

        if action == "cancel":
            # Complete (cancel) the reminder and delete its pre-alerts
            self.reminders.complete_reminder(reminder_id)
            # Cancel scheduled jobs and delete pre-alerts from DB
            alerts = self.reminders.get_alerts_for_reminder(reminder_id)
            for alert in alerts:
                self._cancel_alert_job(alert.id, context)
                self.reminders.delete_reminder(alert.id)
            await update.message.reply_text(
                f"Cancelled reminder: {reminder.title}"
            )

        elif action == "reschedule":
            new_time_iso = modification.get("new_time")
            if not new_time_iso:
                # Could not parse the new time -- enter reschedule flow
                self._reset_state(user_id, "modify reschedule needs time")
                self._user_states[user_id] = UserState(
                    mode=UserMode.AWAITING_RESCHEDULE,
                    reminder_id=reminder_id,
                    reminder_title=reminder.title,
                )
                await update.message.reply_text(
                    f"When should I reschedule '{reminder.title}'?\n"
                    "e.g., 'tomorrow at 3pm' or 'next Monday at 10am'"
                )
                return

            try:
                new_due = datetime.fromisoformat(new_time_iso)
                if new_due.tzinfo is None:
                    from zoneinfo import ZoneInfo
                    new_due = new_due.replace(tzinfo=ZoneInfo(self.settings.timezone))
                new_due_utc = new_due.astimezone(timezone.utc)

                # Cancel scheduled jobs for old alerts before they are deleted
                old_alerts = self.reminders.get_alerts_for_reminder(reminder_id)
                for old_alert in old_alerts:
                    self._cancel_alert_job(old_alert.id, context)

                updated = self.reminders.update_reminder_time(reminder_id, new_due_utc)
                if updated:
                    time_str = formatters.to_local(new_due_utc, self.settings.timezone)
                    await update.message.reply_text(
                        f"Rescheduled '{updated.title}' to {time_str}.\n"
                        "Previous alerts have been cleared.",
                        reply_markup=keyboards.alert_preferences(updated.id),
                    )
                else:
                    await update.message.reply_text("Could not update that reminder.")
            except Exception as e:
                logger.error("Failed to reschedule via modify: %s", e)
                await update.message.reply_text(
                    "Sorry, I couldn't parse that new time. Could you try again?"
                )

        elif action == "change_alert":
            # Enter the alert configuration flow for this reminder
            due_at = reminder.due_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            self._reset_state(user_id, "modify change_alert")
            self._user_states[user_id] = UserState(
                mode=UserMode.AWAITING_ALERT_TIME,
                reminder_id=reminder_id,
                due_at=due_at,
                reminder_title=reminder.title,
            )
            due_str = formatters.to_local(due_at, self.settings.timezone)
            await update.message.reply_text(
                f"Changing alerts for: {reminder.title}\n"
                f"Event time: {due_str}\n\n"
                "Tell me when you'd like to be alerted.\n"
                "e.g., '2 hours before' or 'the morning of at 9am'\n"
                "Say 'done' when finished."
            )

        else:
            await update.message.reply_text(
                f"I'm not sure what to do with '{reminder.title}'. "
                "Try saying 'reschedule', 'cancel', or 'change alerts'."
            )

    # ---- Setup / Config Handler ----

    def _get_current_setting(self, key: str) -> str | None:
        """Read a single setting from the UserConfig DB table.

        Returns the stored value string, or None if not set.
        """
        with get_session() as session:
            row = session.get(UserConfig, key)
            if row:
                session.expunge(row)
                return row.value
        return None

    def _get_effective_setting(self, key: str) -> str:
        """Get the effective value for a setting key.

        Checks UserConfig DB first, then falls back to rules.yaml defaults.
        """
        db_val = self._get_current_setting(key)
        if db_val is not None:
            return db_val

        rules = self.settings.rules
        defaults = {
            "email_check_morning": rules.get("email", {}).get("check_times", ["07:00"])[0],
            "email_check_evening": (
                rules.get("email", {}).get("check_times", ["07:00", "21:30"])[1]
                if len(rules.get("email", {}).get("check_times", [])) > 1
                else "21:30"
            ),
            "quiet_start": rules.get("reminders", {}).get("quiet_hours", ["22:00"])[0],
            "quiet_end": (
                rules.get("reminders", {}).get("quiet_hours", ["22:00", "07:00"])[1]
                if len(rules.get("reminders", {}).get("quiet_hours", [])) > 1
                else "07:00"
            ),
            "stock_symbols": ",".join(
                rules.get("briefing", {}).get("morning", {}).get(
                    "stock_symbols", ["META", "AAPL"]
                )
            ),
        }
        return defaults.get(key, "")

    async def _handle_setup(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        user_id: int, user_message: str, intent_data: dict,
    ) -> None:
        """Handle the 'setup' intent -- bot settings configuration.

        Parses settings from the LLM intent, validates them, shows a
        confirmation preview with old -> new values, and waits for
        user confirmation before applying.
        """
        raw_settings = intent_data.get("settings", [])
        if not raw_settings:
            await update.message.reply_text(
                "I can help you configure settings. Try something like:\n"
                "- \"change morning email check to 6:30am\"\n"
                "- \"add stock TSLA to my watchlist\"\n"
                "- \"set quiet hours from 10pm to 7am\"\n"
                "- \"change evening email check to 9pm\""
            )
            return

        # Validate the parsed settings
        valid_settings, errors = self.llm.validate_setup_settings(raw_settings)

        if errors:
            error_msg = "\n".join(f"  - {e}" for e in errors)
            await update.message.reply_text(
                f"Some settings could not be validated:\n{error_msg}"
            )
            if not valid_settings:
                return

        # Resolve stock_symbols_add / stock_symbols_remove into the
        # effective stock_symbols list before showing the preview.
        resolved_settings = self._resolve_stock_mutations(valid_settings)

        # Build confirmation message with old -> new values
        lines = ["Settings to change:\n"]
        for s in resolved_settings:
            key = s["key"]
            new_val = s["value"]
            old_val = self._get_effective_setting(key)
            label = _SETTING_LABELS.get(key, key)
            lines.append(f"  {label}: {old_val} -> {new_val}")

        lines.append("\nConfirm these changes?")
        summary = "\n".join(lines)

        # Store pending settings in bot_data keyed by a unique ID
        import uuid
        settings_id = str(uuid.uuid4())[:8]
        context.bot_data.setdefault("pending_settings", {})[settings_id] = resolved_settings

        await update.message.reply_text(
            summary,
            reply_markup=keyboards.confirm_settings(settings_id),
        )

    def _resolve_stock_mutations(self, settings: list[dict]) -> list[dict]:
        """Convert stock_symbols_add / stock_symbols_remove into a full
        stock_symbols replacement setting."""
        resolved = []
        current_symbols_str = self._get_effective_setting("stock_symbols")
        current_symbols = [s.strip() for s in current_symbols_str.split(",") if s.strip()]

        for s in settings:
            key = s["key"]
            if key == "stock_symbols_add":
                sym = s["value"]
                if sym not in current_symbols:
                    current_symbols.append(sym)
                resolved.append({"key": "stock_symbols", "value": ",".join(current_symbols)})
            elif key == "stock_symbols_remove":
                sym = s["value"]
                current_symbols = [x for x in current_symbols if x != sym]
                resolved.append({"key": "stock_symbols", "value": ",".join(current_symbols)})
            else:
                resolved.append(s)

        return resolved

    def _apply_settings(self, settings: list[dict], context) -> list[str]:
        """Apply validated settings to UserConfig DB and update in-memory state.

        Returns a list of setting keys that affect email check schedule.
        """
        email_time_keys = {"email_check_morning", "email_check_evening"}
        changed_email_times = False

        with get_session() as session:
            for s in settings:
                key = s["key"]
                value = s["value"]
                existing = session.get(UserConfig, key)
                if existing:
                    existing.value = value
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    from src.db.models import utcnow
                    session.add(UserConfig(key=key, value=value, updated_at=utcnow()))

                # Update in-memory settings
                if key == "email_check_morning":
                    check_times = self.settings.rules.get("email", {}).get("check_times", ["07:00", "21:30"])
                    if len(check_times) > 0:
                        check_times[0] = value
                    else:
                        check_times = [value]
                    self.settings.rules.setdefault("email", {})["check_times"] = check_times
                    changed_email_times = True
                elif key == "email_check_evening":
                    check_times = self.settings.rules.get("email", {}).get("check_times", ["07:00", "21:30"])
                    if len(check_times) > 1:
                        check_times[1] = value
                    else:
                        check_times.append(value)
                    self.settings.rules.setdefault("email", {})["check_times"] = check_times
                    changed_email_times = True
                elif key == "quiet_start":
                    quiet = self.settings.rules.get("reminders", {}).get("quiet_hours", ["22:00", "07:00"])
                    if len(quiet) > 0:
                        quiet[0] = value
                    self.settings.rules.setdefault("reminders", {})["quiet_hours"] = quiet
                elif key == "quiet_end":
                    quiet = self.settings.rules.get("reminders", {}).get("quiet_hours", ["22:00", "07:00"])
                    if len(quiet) > 1:
                        quiet[1] = value
                    self.settings.rules.setdefault("reminders", {})["quiet_hours"] = quiet
                elif key == "stock_symbols":
                    syms = [x.strip() for x in value.split(",") if x.strip()]
                    self.settings.rules.setdefault("briefing", {}).setdefault("morning", {})["stock_symbols"] = syms

        # Reschedule email check jobs if times changed
        if changed_email_times:
            new_times = self.settings.rules.get("email", {}).get("check_times", [])
            scheduler_jobs = context.bot_data.get("scheduler_jobs") if context else None
            if scheduler_jobs is not None:
                scheduler_jobs.reschedule_email_checks(new_times)

        return [s["key"] for s in settings]

    # ---- Callback Query Handler (Step 3F) ----

    async def callback_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        self._track_chat(update, context)

        query = update.callback_query
        await query.answer()
        data = query.data

        if data.startswith("setup_confirm:"):
            settings_id = data.split(":")[1]
            pending = context.bot_data.get("pending_settings", {}).pop(settings_id, None)
            if not pending:
                await query.edit_message_text("Settings expired or already applied.")
                return
            applied = self._apply_settings(pending, context)
            labels = [_SETTING_LABELS.get(k, k) for k in applied]
            await query.edit_message_text(
                "Settings updated:\n" + "\n".join(f"  - {l}" for l in labels)
            )

        elif data.startswith("setup_cancel:"):
            settings_id = data.split(":")[1]
            context.bot_data.get("pending_settings", {}).pop(settings_id, None)
            await query.edit_message_text("Settings change cancelled.")

        elif data.startswith("alert_more:"):
            parent_id = int(data.split(":")[1])
            parent = self.reminders.get_reminder(parent_id)
            if not parent:
                await query.edit_message_text("Reminder not found.")
                return
            due_at = parent.due_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            user_id = update.effective_user.id
            self._reset_state(user_id, "alert_more callback")
            self._user_states[user_id] = UserState(
                mode=UserMode.AWAITING_ALERT_TIME,
                reminder_id=parent_id,
                due_at=due_at,
                reminder_title=parent.title,
            )
            await query.edit_message_text(
                f"Adding alerts for: {parent.title}\n"
                "Tell me when you'd like to be alerted."
            )

        elif data.startswith("alert_"):
            parts = data.split(":")
            alert_type = parts[0].replace("alert_", "")
            reminder_id = int(parts[1])
            reminder = self.reminders.get_reminder(reminder_id)
            if not reminder:
                await query.edit_message_text("Reminder not found 🐰")
                return
            due_at = reminder.due_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)

            alert_configs = {
                "daymorning": [
                    {"offset": "1d_before", "time": "20:00", "label": "Night before"},
                    {"offset": "morning_of", "time": "08:00", "label": "Morning of"},
                ],
                "morning": [
                    {"offset": "morning_of", "time": "08:00", "label": "Morning of"},
                ],
                "1h": [
                    {"offset": "1h_before", "label": "1 hour before"},
                ],
                "none": [],
            }
            pre_alerts = alert_configs.get(alert_type, [])
            if alert_type == "custom":
                user_id = update.effective_user.id
                self._reset_state(user_id, "alert_custom callback")
                self._user_states[user_id] = UserState(
                    mode=UserMode.AWAITING_ALERT_TIME,
                    reminder_id=reminder_id,
                    due_at=due_at,
                    reminder_title=reminder.title,
                )
                await query.edit_message_text(
                    "Tell me how you'd like to be alerted.\n"
                    "e.g., 'the morning of' or '2 hours before'\n"
                    "Say 'done' when finished."
                )
                return

            # Preset alert paths: self-contained, uses reminder_id from callback_data
            self._reset_state(update.effective_user.id, "preset alert selected")
            self._create_pre_alerts(reminder, pre_alerts, due_at, context)
            summary = self._format_alert_summary(pre_alerts)
            text = formatters.format_reminder(reminder, self.settings.timezone)
            await query.edit_message_text(f"⏰ {text}\n\n🔔 Alerts: {summary}\n🐰")

        elif data.startswith("reminder_done:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.complete_reminder(reminder_id)
            await query.edit_message_text("✅ Reminder completed! 🐰")

        elif data.startswith("reminder_snooze_1h:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.snooze_reminder(reminder_id, "1h")
            await query.edit_message_text("💤 Snoozed for 1 hour 🐰")

        elif data.startswith("reminder_snooze_tomorrow:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.snooze_reminder(reminder_id, "tomorrow")
            await query.edit_message_text("💤 Snoozed until tomorrow 🐰")

        elif data.startswith("reminder_repeat_weekly:"):
            reminder_id = int(data.split(":")[1])
            # N4: use Reminder imported at module top
            with get_session() as session:
                reminder = session.get(Reminder, reminder_id)
                if reminder:
                    reminder.recurrence = "weekly"
            await query.edit_message_text("Set to repeat weekly!")

        elif data.startswith("manage_alerts:"):
            reminder_id = int(data.split(":")[1])
            reminder = self.reminders.get_reminder(reminder_id)
            if not reminder:
                await query.edit_message_text("Reminder not found 🐰")
                return
            alerts = self.reminders.get_alerts_for_reminder(reminder_id)
            active_alerts = [a for a in alerts if a.status in ("active", "notified")]
            if active_alerts:
                lines = [f"Alerts for: {reminder.title}\n"]
                for a in active_alerts:
                    t = formatters.to_local(a.due_at, self.settings.timezone)
                    lines.append(f"  - {a.title} ({t})")
                await query.edit_message_text(
                    "\n".join(lines),
                    reply_markup=keyboards.manage_alerts(reminder_id),
                )
            else:
                await query.edit_message_text(
                    f"No active alerts for: {reminder.title}",
                    reply_markup=keyboards.manage_alerts(reminder_id),
                )

        elif data.startswith("add_alert:"):
            reminder_id = int(data.split(":")[1])
            reminder = self.reminders.get_reminder(reminder_id)
            if not reminder:
                await query.edit_message_text("Reminder not found.")
                return
            due_at = reminder.due_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            user_id = update.effective_user.id
            self._reset_state(user_id, "add_alert callback")
            self._user_states[user_id] = UserState(
                mode=UserMode.AWAITING_ALERT_TIME,
                reminder_id=reminder_id,
                due_at=due_at,
                reminder_title=reminder.title,
            )
            due_str = formatters.to_local(due_at, self.settings.timezone)
            await query.edit_message_text(
                f"Add alert for: {reminder.title}\n"
                f"Event time: {due_str}\n\n"
                "Tell me when you'd like to be alerted.\n"
                "e.g., '2 hours before' or 'the morning of at 9am'\n"
                "Say 'done' when finished."
            )

        elif data.startswith("delete_alerts:"):
            reminder_id = int(data.split(":")[1])
            reminder = self.reminders.get_reminder(reminder_id)
            if not reminder:
                await query.edit_message_text("Reminder not found 🐰")
                return
            alerts = self.reminders.get_alerts_for_reminder(reminder_id)
            active_alerts = [a for a in alerts if a.status in ("active", "notified")]
            if not active_alerts:
                await query.edit_message_text(
                    f"No active alerts to delete for: {reminder.title} 🐰"
                )
                return
            await query.edit_message_text(
                f"Select an alert to delete for: {reminder.title}",
                reply_markup=keyboards.delete_alert_actions(active_alerts, self.settings.timezone),
            )

        elif data.startswith("del_alert:"):
            alert_id = int(data.split(":")[1])
            alert = self.reminders.get_reminder(alert_id)
            if not alert:
                await query.edit_message_text("Alert not found 🐰")
                return
            title = alert.title
            # Cancel the scheduled DateTrigger job before deleting from DB
            self._cancel_alert_job(alert_id, context)
            self.reminders.delete_reminder(alert_id)
            await query.edit_message_text(f"Deleted alert: {title} 🐰")

        elif data.startswith("post_done:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.complete_reminder(reminder_id)
            await query.edit_message_text("✅ Marked as done! 🐰")

        elif data.startswith("post_snooze:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.snooze_reminder(reminder_id, "1h")
            await query.edit_message_text("💤 Snoozed for 1 hour 🐰")

        elif data.startswith("post_reschedule:"):
            reminder_id = int(data.split(":")[1])
            reminder = self.reminders.get_reminder(reminder_id)
            if not reminder:
                await query.edit_message_text("Reminder not found 🐰")
                return
            user_id = update.effective_user.id
            self._reset_state(user_id, "post_reschedule callback")
            self._user_states[user_id] = UserState(
                mode=UserMode.AWAITING_RESCHEDULE,
                reminder_id=reminder_id,
                reminder_title=reminder.title,
            )
            await query.edit_message_text(
                f"When should I reschedule '{reminder.title}'?\n"
                "e.g., 'tomorrow at 3pm' or 'next Monday at 10am' 🐰"
            )

        elif data.startswith("email_remind:"):
            email_idx = int(data.split(":")[1])
            user_id = update.effective_user.id
            self._reset_state(user_id, "email_remind callback")
            self._user_states[user_id] = UserState(
                mode=UserMode.IDLE,
                reminder_id=email_idx,
                reminder_title="__email_remind__",
            )
            await query.edit_message_text(
                "When should I remind you? (e.g., 'tomorrow at 3pm')"
            )

        elif data.startswith("email_done:"):
            await query.edit_message_text("Got it, marked as done.")

        elif data.startswith("email_dismiss:"):
            await query.edit_message_text("👋 Dismissed 🐰")

        elif data.startswith("disambig_new_reminder:"):
            # User confirmed they want a new reminder, not an alert
            user_id = update.effective_user.id
            self._reset_state(user_id, "user chose new reminder")
            await query.edit_message_text(
                "OK, exited alert setup. Send me your new reminder."
            )
            # Next text message will go through IDLE -> classify_intent

        elif data.startswith("disambig_retry_alert:"):
            # User wants to try again with alert time
            await query.edit_message_text(
                "No problem. Try again with something like:\n"
                "  'the morning of'\n"
                "  '2 hours before'\n"
                "  '1 minute from now'\n"
                "Or say 'done' to finish."
            )
            # Stay in AWAITING_ALERT_TIME -- state unchanged

        elif data.startswith("note_remind:"):
            note_id = int(data.split(":")[1])
            await query.edit_message_text(
                "When should I remind you about this note? "
                "(e.g., 'every Thursday at 6:30pm')"
            )

        elif data == "confirm":
            await query.edit_message_text("Confirmed.")

        elif data == "cancel":
            await query.edit_message_text("Cancelled.")

    # ---- Conversation Context Management (Step 3F) ----

    def _store_conversation(self, role: str, content: str, user_id: int = 0) -> None:
        """Store a conversation message in the database.

        N5: Includes user_id for per-user conversation tracking.
        """
        with get_session() as session:
            session.add(Conversation(role=role, content=content, user_id=user_id))

    async def _get_conversation_context(self, user_id: int = 0) -> list[dict[str, str]]:
        """Get conversation history for LLM context.

        Returns context_summary entries (if any) followed by the most
        recent messages, up to MAX_ACTIVE_MESSAGES total.

        N5: Filters by user_id for per-user context isolation.
        """
        with get_session() as session:
            from sqlalchemy import select

            # Get any existing summary for this user
            summaries = (
                session.execute(
                    select(Conversation)
                    .where(Conversation.role == "context_summary")
                    .where(Conversation.user_id == user_id)
                    .order_by(Conversation.timestamp.desc())
                    .limit(1)
                )
                .scalars()
                .all()
            )

            # Get recent messages (excluding summaries) for this user
            recent = (
                session.execute(
                    select(Conversation)
                    .where(Conversation.role.in_(["user", "assistant"]))
                    .where(Conversation.user_id == user_id)
                    .order_by(Conversation.timestamp.desc())
                    .limit(MAX_ACTIVE_MESSAGES)
                )
                .scalars()
                .all()
            )
            recent.reverse()

            # C1: expunge all before session closes
            for s in summaries:
                session.expunge(s)
            for msg in recent:
                session.expunge(msg)

        context = []
        for s in summaries:
            context.append({"role": "system", "content": f"Previous context: {s.content}"})
        for msg in recent:
            context.append({"role": msg.role, "content": msg.content})
        return context

    async def _maybe_summarize_context(self, update: Update, user_id: int = 0) -> None:
        """Summarize older messages if conversation exceeds threshold.

        When message count exceeds MAX_ACTIVE_MESSAGES, the oldest
        SUMMARIZE_BATCH_SIZE messages are summarized by the LLM and
        stored as a context_summary entry.

        N5: Filters by user_id for per-user context.
        """
        with get_session() as session:
            from sqlalchemy import func, select

            count = session.execute(
                select(func.count(Conversation.id)).where(
                    Conversation.role.in_(["user", "assistant"]),
                    Conversation.user_id == user_id,
                )
            ).scalar()

        if count and count > MAX_ACTIVE_MESSAGES:
            # Get the oldest messages to summarize
            with get_session() as session:
                from sqlalchemy import select

                oldest = (
                    session.execute(
                        select(Conversation)
                        .where(Conversation.role.in_(["user", "assistant"]))
                        .where(Conversation.user_id == user_id)
                        .order_by(Conversation.timestamp.asc())
                        .limit(SUMMARIZE_BATCH_SIZE)
                    )
                    .scalars()
                    .all()
                )

                if not oldest:
                    return

                messages_to_summarize = [
                    {"role": m.role, "content": m.content} for m in oldest
                ]
                oldest_ids = [m.id for m in oldest]

            # If circuit breaker is open, fall back to simple truncation
            if self.llm.circuit_breaker.is_open:
                with get_session() as session:
                    for oid in oldest_ids:
                        obj = session.get(Conversation, oid)
                        if obj:
                            session.delete(obj)
                return

            summary = await self.llm.summarize_conversation(messages_to_summarize)

            with get_session() as session:
                # Merge with existing summary if present
                from sqlalchemy import select

                existing = (
                    session.execute(
                        select(Conversation)
                        .where(Conversation.role == "context_summary")
                        .where(Conversation.user_id == user_id)
                        .order_by(Conversation.timestamp.desc())
                        .limit(1)
                    )
                    .scalar_one_or_none()
                )
                if existing:
                    existing.content = f"{existing.content}\n\n{summary}"
                else:
                    session.add(
                        Conversation(
                            role="context_summary",
                            content=summary,
                            user_id=user_id,
                        )
                    )

                # Delete the summarized messages
                for oid in oldest_ids:
                    obj = session.get(Conversation, oid)
                    if obj:
                        session.delete(obj)

            # Notify user
            try:
                await update.message.reply_text(
                    "Older messages in this conversation have been summarized "
                    "to stay within my context window."
                )
            except Exception:
                pass
