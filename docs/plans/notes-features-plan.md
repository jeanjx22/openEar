# Enhanced Notes/Logs Features -- Implementation Plan

## Summary

Four features that extend the openEar note system: auto-logging important conversational info, searchable notes via natural language, family activity logging, and note-to-reminder linking.

---

## Dependency Table

| Task | Feature | Depends On | Can Parallelize With |
|------|---------|------------|----------------------|
| 1    | F2: Search | None | Tasks 2, 3, 4, 5 |
| 2    | F3: Activity Log | None | Tasks 1, 3, 4, 5 |
| 3    | F2: Search | Task 1 | Tasks 2, 4, 5 |
| 4    | F3: Activity Log | Task 2 | Tasks 1, 3, 5 |
| 5    | F1: Auto-log | None | Tasks 1, 2, 3, 4 |
| 6    | F2: Search | Tasks 1, 3 | Tasks 4, 7, 8 |
| 7    | F1: Auto-log | Task 5 | Tasks 6, 8 |
| 8    | F4: Note-Reminder | None | Tasks 6, 7 |
| 9    | F3: Activity Log | Tasks 2, 4 | Tasks 6, 7, 8 |
| 10   | F1: Auto-log | Tasks 5, 7 | Task 11 |
| 11   | F4: Note-Reminder | Task 8 | Task 10 |
| 12   | All | Tasks 6, 7, 9, 10, 11 | None |

Parallel execution groups:
- **Group A** (independent, do first): Tasks 1, 2, 3, 4, 5, 8
- **Group B** (needs Group A): Tasks 6, 7, 9, 10, 11
- **Group C** (final): Task 12

---

## Feature 2: Searchable Notes

### Task 1 -- Add `search_notes` intent to LLM classifier

**File:** `src/services/llm_service.py`
**What to change:**

In `classify_intent`, add `"search_notes"` to the intent list in the system prompt. Insert it between `"note"` and `"weather"`:

```
Old (line ~215):
- "note": save a note or piece of information

New:
- "note": save a note or piece of information
- "search_notes": user is asking about previously saved notes or trying to recall something they noted (e.g. "what did I note about X?", "find my note on Y", "what was Aaron's allergy?")
```

Also update the single request format comment to include `"search_notes"` in the intent enum (line ~233).

**Verification:** Run `python -c "from src.services.llm_service import LLMService; print('import ok')"` via `buck run` or direct import test.

---

### Task 3 -- Add `search_notes` intent handler in handlers.py

**File:** `src/bot/handlers.py`
**What to change:**

In `_process_single_intent`, add a new `elif` branch after the `"note"` handler (after line 717):

```python
elif intent == "search_notes":
    content = intent_data.get("content", user_message)
    # Search by content and by tag, merge and deduplicate
    content_matches = self.notes.search_notes(content)
    # Extract likely search terms for tag search
    tags = intent_data.get("tags", [])
    tag_matches = []
    for tag in tags:
        tag_matches.extend(self.notes.search_by_tag(tag))
    # Deduplicate by note id
    seen_ids = set()
    all_matches = []
    for note in content_matches + tag_matches:
        if note.id not in seen_ids:
            seen_ids.add(note.id)
            all_matches.append(note)
    if all_matches:
        reply = formatters.format_note_search_results(
            all_matches, content, self.settings.timezone
        )
    else:
        reply = f"No notes found matching '{content}'."
    await update.message.reply_text(reply)
```

**Verification:** Confirm the file parses: `python -c "import ast; ast.parse(open('src/bot/handlers.py').read()); print('ok')"`.

---

### Task 6 -- Add `format_note_search_results` formatter

**File:** `src/bot/formatters.py`
**What to change:**

Add a new function after `format_note_list` (after line 161):

```python
def format_note_search_results(
    notes: list, query: str, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format note search results."""
    if not notes:
        return f"No notes found matching '{query}'."

    lines = [f"Found {len(notes)} note(s) matching '{query}':\n"]
    for n in notes:
        created = to_local(n.created_at, tz_name)
        tags = ""
        if n.tags and n.tags != "[]":
            import json
            try:
                tag_list = json.loads(n.tags)
                if tag_list:
                    tags = f" [{', '.join(tag_list)}]"
            except Exception:
                pass
        lines.append(f"  #{n.id} ({created}){tags}:")
        lines.append(f"    {n.content}")
        lines.append("")
    return "\n".join(lines)
```

**Verification:** `python -c "from src.bot.formatters import format_note_search_results; print('ok')"`.

---

## Feature 3: Family Activity Log

### Task 2 -- Add `activity_log` intent to LLM classifier

**File:** `src/services/llm_service.py`
**What to change:**

In `classify_intent`, add `"activity_log"` intent to the system prompt. Insert after the new `"search_notes"` line:

```
- "activity_log": user is logging what a family member did or is doing (e.g. "husband went to tennis", "Aaron had a playdate at Oscar's", "August napped 2 hours")
- "search_activity": user is asking what someone did recently (e.g. "what did husband do this week?", "what has Aaron done lately?")
```

For `"activity_log"`, instruct the LLM to extract: `"who"` (family member), `"activity"` (what they did), `"content"` (full text). For `"search_activity"`, extract `"who"` and `"time_range"` (e.g. "this week", "today", "lately").

Add to the system prompt after the setup format example:

```
Activity log format:
{"intent": "activity_log", "content": "husband went to tennis", "who": "husband", "activity": "tennis", "tags": ["activity_log", "husband"]}

Search activity format:
{"intent": "search_activity", "content": "what did husband do this week", "who": "husband", "time_range": "this week", "tags": ["activity_log"]}
```

**Verification:** Same import check as Task 1.

---

### Task 4 -- Add `get_notes_by_tag_since` to NoteService

**File:** `src/services/note_service.py`
**What to change:**

Add a method after `search_by_tag` (after line 87):

```python
def get_notes_by_tag_since(
    self, tag: str, since: datetime | None = None
) -> list[Note]:
    """Get notes with a specific tag, optionally filtered to a time range.

    Args:
        tag: Tag to search for (case-insensitive).
        since: If provided, only return notes created after this datetime.
    """
    with get_session() as session:
        stmt = select(Note).where(Note.tags.ilike(f'%"{tag}"%'))
        if since:
            stmt = stmt.where(Note.created_at >= since)
        stmt = stmt.order_by(Note.created_at.desc())
        candidates = list(session.execute(stmt).scalars().all())
        results = []
        for note in candidates:
            session.expunge(note)
            try:
                note_tags = json.loads(note.tags)
                if tag.lower() in [t.lower() for t in note_tags]:
                    results.append(note)
            except json.JSONDecodeError:
                pass
        return results
```

Also add `from datetime import datetime` to the imports at the top of the file (after line 5):

```python
from datetime import datetime
```

**Verification:** `python -c "from src.services.note_service import NoteService; print('ok')"`.

---

### Task 9 -- Add `activity_log` and `search_activity` intent handlers

**File:** `src/bot/handlers.py`
**What to change:**

In `_process_single_intent`, add two new `elif` branches after the `search_notes` handler:

```python
elif intent == "activity_log":
    who = intent_data.get("who", "")
    activity = intent_data.get("activity", "")
    content = intent_data.get("content", user_message)
    tags = ["activity_log"]
    if who:
        tags.append(who.lower())
    note = self.notes.save_note(content, tags)
    await update.message.reply_text(f"Logged! {who}'s activity: {activity}")

elif intent == "search_activity":
    who = intent_data.get("who", "")
    time_range = intent_data.get("time_range", "this week")
    since = self._parse_time_range(time_range)
    tag = who.lower() if who else "activity_log"
    activities = self.notes.get_notes_by_tag_since(tag, since)
    # Also filter for activity_log tag if searching by person
    if who:
        activities = [
            a for a in activities
            if "activity_log" in (json.loads(a.tags) if a.tags else [])
        ]
    if activities:
        reply = formatters.format_activity_log(
            activities, who, self.settings.timezone
        )
    else:
        reply = f"No activities found for {who or 'anyone'} ({time_range})."
    await update.message.reply_text(reply)
```

Also add a helper method `_parse_time_range` to the `BotHandlers` class:

```python
def _parse_time_range(self, time_range: str) -> datetime | None:
    """Parse a natural language time range into a since-datetime."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    lower = time_range.lower().strip()
    if "today" in lower:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif "yesterday" in lower:
        return (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif "week" in lower:
        return now - timedelta(days=7)
    elif "month" in lower:
        return now - timedelta(days=30)
    return now - timedelta(days=7)  # default to last week
```

Add `import json` to the module imports if not already present (it is, on line 17).

**Verification:** `python -c "import ast; ast.parse(open('src/bot/handlers.py').read()); print('ok')"`.

---

### Task 9b -- Add `format_activity_log` formatter

**File:** `src/bot/formatters.py`
**What to change:**

Add after `format_note_search_results`:

```python
def format_activity_log(
    activities: list, who: str, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format activity log entries chronologically."""
    if not activities:
        return f"No activities logged for {who}."

    # Sort chronologically (oldest first for reading order)
    sorted_activities = sorted(activities, key=lambda a: a.created_at)
    name = who.title() if who else "Family"
    lines = [f"Activity log for {name}:\n"]
    current_day = None
    tz = ZoneInfo(tz_name)
    for a in sorted_activities:
        dt = a.created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tz)
        day_str = local_dt.strftime("%A %b %d")
        time_str = local_dt.strftime("%-I:%M %p")
        if day_str != current_day:
            current_day = day_str
            lines.append(f"\n  {day_str}:")
        lines.append(f"    {time_str} -- {a.content}")
    return "\n".join(lines)
```

**Verification:** `python -c "from src.bot.formatters import format_activity_log; print('ok')"`.

---

## Feature 1: Auto-log Conversations

### Task 5 -- Add `should_auto_save` LLM method

**File:** `src/services/llm_service.py`
**What to change:**

Add a new method after `classify_intent` (after line 255):

```python
async def should_auto_save(self, user_message: str, bot_response: str) -> dict | None:
    """Check if a conversation contains information worth auto-saving.

    Returns a dict with keys: should_save (bool), content (str to save),
    tags (list[str]). Returns None if LLM is unavailable.
    """
    system_prompt = """Analyze this conversation turn. Determine if the user shared important personal/family information that should be saved for future reference.

SAVE-WORTHY information (return should_save: true):
- Allergies, medical info, dietary restrictions
- Schedules, routines, recurring activities
- Preferences (food, activities, etc.)
- Important dates (birthdays, anniversaries)
- Contact info, addresses
- School or childcare details
- Names of friends, teachers, doctors

NOT save-worthy (return should_save: false):
- General chitchat, greetings
- Questions that were already answered
- Complaints, venting
- Information already in the system
- Requests for actions (reminders, weather, etc.)

Return ONLY a JSON object:
{"should_save": true/false, "content": "<concise fact to save>", "tags": ["<relevant_tags>"]}

If should_save is false, content and tags can be empty."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"User said: {user_message}\n\nBot replied: {bot_response}"},
    ]
    result = await self.call_groq(messages, temperature=0.1)
    if result is None:
        return None
    try:
        return json.loads(_clean_json(result))
    except json.JSONDecodeError:
        logger.warning("Failed to parse auto-save JSON: %s", result)
        return None
```

**Verification:** `python -c "from src.services.llm_service import LLMService; print('ok')"`.

---

### Task 7 -- Wire auto-save check into general conversation handler

**File:** `src/bot/handlers.py`
**What to change:**

In `_process_single_intent`, in the `else` (general conversation) branch (lines 786-799), add an auto-save check after storing the conversation turn. The current code is:

```python
else:
    # General conversation with context management
    history = await self._get_conversation_context(user_id)
    response = await self.llm.chat(
        user_message, history, self.settings.persona
    )
    await update.message.reply_text(response)

    # Store conversation turn
    self._store_conversation("user", user_message, user_id)
    self._store_conversation("assistant", response, user_id)

    # Check if summarization is needed
    await self._maybe_summarize_context(update, user_id)
```

Replace with:

```python
else:
    # General conversation with context management
    history = await self._get_conversation_context(user_id)
    response = await self.llm.chat(
        user_message, history, self.settings.persona
    )
    await update.message.reply_text(response)

    # Store conversation turn
    self._store_conversation("user", user_message, user_id)
    self._store_conversation("assistant", response, user_id)

    # Auto-save check: detect save-worthy information
    await self._maybe_auto_save(update, user_message, response)

    # Check if summarization is needed
    await self._maybe_summarize_context(update, user_id)
```

**Verification:** `python -c "import ast; ast.parse(open('src/bot/handlers.py').read()); print('ok')"`.

---

### Task 10 -- Implement `_maybe_auto_save` method

**File:** `src/bot/handlers.py`
**What to change:**

Add a new method to the `BotHandlers` class, after `_maybe_summarize_context`:

```python
async def _maybe_auto_save(
    self, update: Update, user_message: str, bot_response: str,
) -> None:
    """Check if the conversation contains save-worthy info and auto-save it.

    Only runs if the circuit breaker is not open (to avoid unnecessary
    LLM calls when the service is degraded).
    """
    if self.llm.circuit_breaker.is_open:
        return

    try:
        result = await self.llm.should_auto_save(user_message, bot_response)
        if result and result.get("should_save"):
            content = result.get("content", user_message)
            tags = result.get("tags", [])
            tags.append("auto_saved")
            self.notes.save_note(content, tags)
            await update.message.reply_text("Noted that!")
    except Exception as e:
        logger.debug("Auto-save check failed (non-critical): %s", e)
```

**Verification:** `python -c "import ast; ast.parse(open('src/bot/handlers.py').read()); print('ok')"`.

---

## Feature 4: Notes Linked to Reminders

### Task 8 -- Add `check_future_event` LLM method

**File:** `src/services/llm_service.py`
**What to change:**

Add a new method after `should_auto_save`:

```python
async def check_future_event(self, note_content: str, timezone_str: str) -> dict | None:
    """Check if a note's content mentions a future event that could use a reminder.

    Returns a dict with keys: has_future_event (bool), event_description (str),
    suggested_time (str, natural language). Returns None if LLM unavailable.
    """
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    from zoneinfo import ZoneInfo

    now_local = _dt.now(_tz.utc).astimezone(ZoneInfo(timezone_str))

    system_prompt = f"""Analyze this note content. Determine if it mentions a future event, appointment, or deadline that would benefit from a reminder.

Current date/time: {now_local.strftime("%A %B %d, %Y %I:%M %p")}

Return ONLY a JSON object:
{{"has_future_event": true/false, "event_description": "<what the event is>", "suggested_time": "<when, in natural language>"}}

Examples:
- "Aaron has a dentist appointment next Tuesday at 3pm" -> {{"has_future_event": true, "event_description": "Aaron's dentist appointment", "suggested_time": "next Tuesday at 3pm"}}
- "Aaron is allergic to eggs" -> {{"has_future_event": false, "event_description": "", "suggested_time": ""}}
- "Piano recital on May 15th" -> {{"has_future_event": true, "event_description": "Piano recital", "suggested_time": "May 15th"}}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": note_content},
    ]
    result = await self.call_groq(messages, temperature=0.1)
    if result is None:
        return None
    try:
        return json.loads(_clean_json(result))
    except json.JSONDecodeError:
        logger.warning("Failed to parse future event JSON: %s", result)
        return None
```

**Verification:** `python -c "from src.services.llm_service import LLMService; print('ok')"`.

---

### Task 11 -- Wire note-to-reminder offer into note save flows

**File:** `src/bot/handlers.py`
**What to change:**

**11a.** In `cmd_save_note` (line 459), replace the existing recurring-pattern check with a future-event check. Change lines 479-487:

```python
# Old code:
has_recurring = any(
    word in content.lower()
    for word in ["every", "weekly", "daily", "monthly", "each"]
)
markup = keyboards.note_followup(note.id) if has_recurring else None
await update.message.reply_text(
    f"Saved! {reply}", reply_markup=markup
)
```

to:

```python
# Check for future event to offer reminder
markup = None
has_recurring = any(
    word in content.lower()
    for word in ["every", "weekly", "daily", "monthly", "each"]
)
if has_recurring:
    markup = keyboards.note_followup(note.id)
else:
    future_event = await self.llm.check_future_event(
        content, self.settings.timezone
    )
    if future_event and future_event.get("has_future_event"):
        markup = keyboards.note_remind_with_context(
            note.id, future_event.get("event_description", "")
        )
await update.message.reply_text(
    f"Saved! {reply}", reply_markup=markup
)
```

**11b.** Do the same for the `"note"` intent handler in `_process_single_intent` (line 712). After saving the note, add the future-event check:

```python
elif intent == "note":
    content = intent_data.get("content", user_message)
    tags = intent_data.get("tags", [])
    note = self.notes.save_note(content, tags)
    reply = formatters.format_note(note, self.settings.timezone)
    # Check for future event
    markup = None
    future_event = await self.llm.check_future_event(
        content, self.settings.timezone
    )
    if future_event and future_event.get("has_future_event"):
        markup = keyboards.note_remind_with_context(
            note.id, future_event.get("event_description", "")
        )
    await update.message.reply_text(f"Saved! {reply}", reply_markup=markup)
```

**11c.** Add the new keyboard builder in `src/bot/keyboards.py`. After `note_followup` (after line 138):

```python
def note_remind_with_context(note_id: int, event_desc: str) -> InlineKeyboardMarkup:
    """Inline keyboard offering to set a reminder for a note with a future event."""
    # Truncate event description for button text (Telegram limit is 64 bytes)
    label = f"Set reminder for {event_desc[:30]}?" if event_desc else "Set a reminder?"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"note_remind:{note_id}",
                ),
            ]
        ]
    )
```

**Verification:** `python -c "import ast; ast.parse(open('src/bot/handlers.py').read()); ast.parse(open('src/bot/keyboards.py').read()); print('ok')"`.

---

## Task 12 -- Integration test and rules.yaml update

### 12a. Update rules.yaml with activity log config

**File:** `config/rules.yaml`
**What to change:**

Add under the `notes:` section (after line 42):

```yaml
notes:
  auto_suggest_reminder: true
  auto_save:
    enabled: true
    confirm_message: "Noted that!"
  activity_log:
    enabled: true
    default_search_range: "7d"
```

### 12b. Manual integration test checklist

Run these in the Telegram bot to verify end to end:

1. **Search notes:** Send "what did I note about allergies?" -- should return matching notes.
2. **Activity log:** Send "husband went to tennis" -- should log as activity.
3. **Search activity:** Send "what did husband do this week?" -- should return activity log.
4. **Auto-save:** Send "Aaron's teacher is Ms. Johnson" in general conversation -- bot should reply normally, then auto-save the info.
5. **Note-to-reminder:** Send "/note Aaron has dentist appointment next Tuesday at 3pm" -- should offer "Set reminder?" button.
6. **Recurring note:** Send "/note take vitamins every morning" -- should still offer "Set reminder?" button (existing behavior preserved).

**Verification:** `python -c "import yaml; yaml.safe_load(open('config/rules.yaml')); print('ok')"`.

---

## File Change Summary

| File | Tasks | Type of Change |
|------|-------|----------------|
| `src/services/llm_service.py` | 1, 2, 5, 8 | Add intents to classifier, add 2 new LLM methods |
| `src/services/note_service.py` | 4 | Add `get_notes_by_tag_since` method |
| `src/bot/handlers.py` | 3, 7, 9, 10, 11 | Add 4 intent handlers, auto-save hook, helper methods |
| `src/bot/formatters.py` | 6, 9b | Add 2 formatter functions |
| `src/bot/keyboards.py` | 11c | Add 1 keyboard builder |
| `config/rules.yaml` | 12a | Add auto_save and activity_log config |

## Estimated Total Time

12 tasks at 2-5 minutes each: ~40-60 minutes.

With parallel execution (Groups A/B/C): ~25-35 minutes.
