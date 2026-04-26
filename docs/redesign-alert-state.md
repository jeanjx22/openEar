# State Machine Redesign: Alert vs. Reminder Confusion

## The Problem

When the bot asks "Want to add more alerts?", the user's next message is routed through a fragile regex-based classifier (lines 410-414 of `handlers.py`):

```python
new_intent_signals = ["remind me to ", "remind me about ", "set a reminder",
                      "what's the weather", "how's ", "how is ",
                      "/start", "/help", "/status", "/reminders", "/notes",
                      "/briefing", "note:", "note "]
if any(lower_msg.startswith(s) for s in new_intent_signals):
    # break out of alert mode
```

This fails in both directions:

- **False positive (breaks out when it should not)**: "remind me the morning of" starts with "remind me" and gets treated as a new reminder intent, when the user is clearly adding an alert.
- **False negative (stays in alert mode when it should not)**: "I need to schedule dentist Thursday 2pm" does not match any signal, so it gets sent to `parse_alert_time`, which either fails silently or creates a wrong alert.

The root cause: **message routing is decided by regex pattern matching on content**, instead of by an explicit user state.

---

## Design: Explicit User State Machine

### States

```
                +------------------+
                |       IDLE       |<---------+----------+---------+
                | (intent classify)|          |          |         |
                +--------+---------+          |          |         |
                         |                    |          |         |
              intent == "reminder"            |          |         |
                         |                    |          |         |
                         v                    |          |         |
                  [create reminder]            |          |         |
                  [show alert_preferences KB]  |          |         |
                         |                    |          |         |
            +------------+-------------+      |          |         |
            |                          |      |          |         |
     user taps preset            user taps    |          |         |
     (daymorning, 1h, etc)       "Custom"     |          |         |
            |                          |      |          |         |
            v                          v      |          |         |
      [create alerts]     +--------------------+         |         |
      [return to IDLE]    | AWAITING_ALERT_TIME|         |         |
                          | (per-reminder ctx) |         |         |
                          +--------+-----------+         |         |
                                   |                     |         |
                          +--------+--------+            |         |
                          |        |        |            |         |
                      text msg  "done"   /command        |         |
                          |        |     or button       |         |
                          v        v        |            |         |
                   [parse_alert_   |   [clear state]-----+         |
                    time via LLM]  |                               |
                          |        +------>[IDLE]                   |
                   +------+------+                                 |
                   |             |                                  |
              parsed OK     parse failed                           |
                   |             |                                  |
                   v             v                                  |
            [create alert]  [disambiguate KB]                      |
            [ask "more?"]   "Could not parse.                      |
                   |         New reminder?"                         |
                   |         [Yes, new] [No, try again]            |
                   |              |           |                     |
                   |         tap "Yes"   tap "No"                  |
                   |              |           |                     |
                   |        [clear state]  [stay in                |
                   |        [re-classify]   AWAITING_ALERT_TIME]   |
                   |              |                                 |
                   +-->[AWAITING_ALERT_TIME]                       |
                                                                   |
                                                                   |
                +-----------------------+                          |
                | AWAITING_RESCHEDULE   |--------------------------+
                | (per-reminder ctx)    |  (on success or cancel)
                +-----------+-----------+
                            |
                       text message
                            |
                            v
                  [parse_reminder_time]
                     |            |
                 parsed OK    parse failed
                     |            |
                     v            v
              [update reminder]  "Couldn't parse,
              [clear -> IDLE]     try again"
                                 [stay in AWAITING_RESCHEDULE]
```

### State Transitions Summary

| Current State | Trigger | Action | Next State |
|---|---|---|---|
| IDLE | text message | `classify_intent()` | depends on intent |
| IDLE | /command | run command handler | IDLE |
| AWAITING_ALERT_TIME | text "done" / "no" / "that's all" | clear context, confirm | IDLE |
| AWAITING_ALERT_TIME | /command | clear context, run command | IDLE |
| AWAITING_ALERT_TIME | inline button tap | clear context (if needed), handle callback | depends on callback |
| AWAITING_ALERT_TIME | any other text | `parse_alert_time()` | see below |
| AWAITING_ALERT_TIME | parse succeeds | create alert, ask "more?" | AWAITING_ALERT_TIME |
| AWAITING_ALERT_TIME | parse fails | show disambiguate keyboard | AWAITING_ALERT_TIME (until button tap) |
| AWAITING_RESCHEDULE | text message | `parse_reminder_time()` | IDLE on success, stay on fail |
| AWAITING_RESCHEDULE | /command | clear context, run command | IDLE |

### Key Principle: No Regex Escape Hatch

In `AWAITING_ALERT_TIME`, there is NO content-based detection of "is this a new intent?". ALL text goes to `parse_alert_time`. If the LLM cannot parse it, we ask the user explicitly with a keyboard. The user's tap decides the routing, not a regex.

---

## Changes by File

### 1. `src/bot/handlers.py` -- The Core Change

#### A. Replace `_pending_reminder_context` with typed `UserState`

Replace the untyped dict with a proper dataclass at the top of the file:

```python
from dataclasses import dataclass
from enum import Enum, auto
from datetime import datetime


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
```

In `BotHandlers.__init__`, replace:
```python
self._pending_reminder_context: dict[int, dict] = {}
```
with:
```python
self._user_states: dict[int, UserState] = {}
```

Add helpers:
```python
def _get_state(self, user_id: int) -> UserState:
    return self._user_states.setdefault(user_id, UserState())

def _reset_state(self, user_id: int, reason: str) -> None:
    old = self._user_states.pop(user_id, None)
    if old and old.mode != UserMode.IDLE:
        logger.info(
            "Reset state for user %d (reason: %s): was %s, reminder_id=%s",
            user_id, reason, old.mode.name, old.reminder_id,
        )
```

#### B. Rewrite `handle_message` top-level flow

The new `handle_message` dispatches on `state.mode` first, with no regex:

```python
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
        await self._handle_alert_time_input(update, user_id, user_message, state)
        return

    # --- IDLE: classify intent normally ---
    await self._handle_idle_message(update, user_id, user_message)
```

#### C. New method `_handle_alert_time_input` (replaces the regex block)

This is the critical change. ALL text goes to the LLM. No regex escape hatch.

```python
# Explicit exit phrases -- these are NOT about content detection.
# They are the user saying "I'm done configuring alerts."
_DONE_PHRASES = frozenset({"done", "no", "nope", "that's it", "that's all",
                            "no more", "nothing else", "i'm good", "all set"})

async def _handle_alert_time_input(
    self, update: Update, user_id: int, user_message: str, state: UserState,
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
            self.reminders.create_reminder(
                title=f"Alert: {reminder.title}",
                due_at=alert_utc,
                source="pre_alert",
                source_ref=str(reminder.id),
                alert_label=label,
            )
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
```

#### D. New method `_handle_reschedule_input`

Extracted from the existing inline code, but now cleaner:

```python
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
```

#### E. `_handle_idle_message` (existing intent classification, extracted)

This is the current `else` branch of `handle_message`, moved to its own method for clarity. No changes to intent classification logic itself.

```python
async def _handle_idle_message(
    self, update: Update, user_id: int, user_message: str,
) -> None:
    """Handle text input in IDLE state -- runs intent classification."""
    intent_data = await self.llm.classify_intent(user_message)
    intent = intent_data.get("intent", "general")

    if intent == "reminder":
        await self._handle_new_reminder(update, user_id, user_message)
    elif intent == "weather":
        # ... existing weather code ...
    elif intent == "stock":
        # ... existing stock code ...
    elif intent == "news":
        # ... existing news code ...
    elif intent == "note":
        # ... existing note code ...
    else:
        # ... existing general conversation code ...
```

#### F. Update callback_handler for new state management

All callback paths that set `_pending_reminder_context` must be updated to set `_user_states` instead. For example:

```python
elif data.startswith("alert_custom:"):
    reminder_id = int(data.split(":")[1])
    reminder = self.reminders.get_reminder(reminder_id)
    if not reminder:
        await query.edit_message_text("Reminder not found.")
        return
    due_at = reminder.due_at
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
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
```

Same pattern for `alert_more:`, `add_alert:`, and `post_reschedule:` callbacks.

#### G. New callback: `disambiguate_new_reminder` and `disambiguate_retry_alert`

Handle the disambiguate keyboard taps:

```python
elif data.startswith("disambig_new_reminder:"):
    # User confirmed they want a new reminder, not an alert
    reminder_id = int(data.split(":")[1])
    self._reset_state(user_id, "user chose new reminder")
    await query.edit_message_text(
        "OK, exited alert setup. Send me your new reminder."
    )
    # Next text message will go through IDLE -> classify_intent

elif data.startswith("disambig_retry_alert:"):
    # User wants to try again with alert time
    reminder_id = int(data.split(":")[1])
    await query.edit_message_text(
        "No problem. Try again with something like:\n"
        "  'the morning of'\n"
        "  '2 hours before'\n"
        "  '1 minute from now'\n"
        "Or say 'done' to finish."
    )
    # Stay in AWAITING_ALERT_TIME -- state unchanged
```

### 2. `src/bot/keyboards.py` -- One New Keyboard

Add one new keyboard function for the disambiguation prompt:

```python
def disambiguate_alert_or_reminder(reminder_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown when parse_alert_time fails in AWAITING_ALERT_TIME state.

    Lets the user explicitly choose: exit to create a new reminder,
    or stay and retry the alert time input.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Yes, new reminder",
                    callback_data=f"disambig_new_reminder:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "No, try again",
                    callback_data=f"disambig_retry_alert:{reminder_id}",
                ),
            ]
        ]
    )
```

### 3. `src/services/llm_service.py` -- No Changes Required

The `parse_alert_time` method already does exactly what we need: it takes text, returns parsed alert(s) or `None`. The problem was never in the LLM service -- it was in how `handlers.py` decided whether to call `parse_alert_time` or `classify_intent`. With the state machine, that decision is made by `state.mode`, not by regex.

### 4. `src/bot/formatters.py` -- No Changes Required

---

## Scenario Walkthroughs

These are the four scenarios that demonstrate the current bug and how the state machine fixes them.

### Scenario 1: "Remind me couple therapy Monday 4pm" then "the morning of"

**Current behavior**: "the morning of" does not match `new_intent_signals` (it does not start with "remind me to" or any other signal). It goes to `parse_alert_time`, which works correctly. This scenario already works.

**New behavior**: Identical. State is `AWAITING_ALERT_TIME`, text goes to `parse_alert_time`, which parses "the morning of" correctly.

### Scenario 2: "Remind me couple therapy Monday 4pm" then "remind me the night before"

**Current behavior (THE BUG)**: "remind me the night before" starts with "remind me", which matches `new_intent_signals`. The bot clears the alert context and sends "Exited alert setup for Couple therapy. Processing your new request..." Then `classify_intent` classifies "remind me the night before" as a new reminder intent. The bot creates a second reminder called "The night before" instead of an alert for the couple therapy reminder.

**New behavior**: State is `AWAITING_ALERT_TIME`. No regex check. "remind me the night before" goes directly to `parse_alert_time`, which correctly parses it as an absolute alert on the night before the event. Alert is created. User is asked "Want to add more?"

### Scenario 3: "Remind me couple therapy Monday 4pm" then "I need to schedule dentist Thursday 2pm"

**Current behavior**: "I need to schedule dentist Thursday 2pm" does not match `new_intent_signals`. It goes to `parse_alert_time`, which either fails (returning None and showing "Sorry, I couldn't understand that time") or, worse, tries to interpret "Thursday 2pm" as an alert time and creates a nonsensical alert.

**New behavior**: State is `AWAITING_ALERT_TIME`. "I need to schedule dentist Thursday 2pm" goes to `parse_alert_time`. The LLM cannot parse "I need to schedule dentist Thursday 2pm" as a relative-to-event alert time, so it returns `None`. The bot shows the disambiguate keyboard:

> "I couldn't parse that as an alert time. Did you mean to set a new reminder instead?"
> [Yes, new reminder] [No, try again]

User taps "Yes, new reminder". State resets to IDLE. Next message goes through `classify_intent`.

### Scenario 4: "Remind me couple therapy Monday 4pm" then "1 minute later"

**Current behavior**: "1 minute later" does not match `new_intent_signals`. It goes to `parse_alert_time`, which interprets "1 minute" as `from_now` type (1 minute from now). This is correct behavior for a quick test alert, and it works.

**New behavior**: Identical. State is `AWAITING_ALERT_TIME`, "1 minute later" goes to `parse_alert_time`, which parses it as `from_now` with minutes=1.

---

## Migration Checklist

All changes are in-memory state only (no DB schema changes). The migration is:

1. Define `UserMode` enum and `UserState` dataclass in `handlers.py`
2. Replace `_pending_reminder_context: dict[int, dict]` with `_user_states: dict[int, UserState]`
3. Replace `_clear_pending_context()` with `_reset_state()`
4. Extract `_handle_alert_time_input()` -- remove ALL regex content detection
5. Extract `_handle_reschedule_input()` -- same logic, cleaner structure
6. Extract `_handle_idle_message()` -- existing intent classification
7. Rewrite `handle_message()` as a 3-way dispatch on `state.mode`
8. Update all callback paths that set pending context to use `UserState`
9. Add `disambiguate_alert_or_reminder()` keyboard to `keyboards.py`
10. Add `disambig_new_reminder:` and `disambig_retry_alert:` callback handlers
11. Test all four scenarios manually

---

## What This Does NOT Change

- LLM prompts (no changes to `llm_service.py`)
- Reminder storage/DB schema
- Alert creation logic (`_create_pre_alerts`, `_format_alert_summary`)
- Keyboard layouts for existing flows (preset alerts, manage alerts, etc.)
- Conversation context management
- Command handlers (`/start`, `/help`, `/reminders`, etc.)

The entire fix is about **when to call `parse_alert_time` vs `classify_intent`**, and the answer is: look at `state.mode`, not at the message text.
