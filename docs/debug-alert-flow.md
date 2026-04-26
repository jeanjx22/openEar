# Alert Flow Architecture Bug Analysis

## Core Problem

`handle_message` uses string-matching heuristics (`new_intent_signals`) to decide whether text from a user in the custom-alert flow is an alert time or a new intent. This is fundamentally broken -- the two concepts overlap in natural language.

---

## Scenario Traces

### A. In custom alert flow, types "remind me 1 minute later"

**Expected:** Parsed as an alert time (1 minute from now).
**Actual:** Exits the alert flow and creates a new reminder.

Code path:
1. `handlers.py:360` -- `pending` exists, `awaiting_custom_alerts` is True.
2. `handlers.py:409` -- `lower_msg = "remind me 1 minute later"`.
3. `handlers.py:410-414` -- `new_intent_signals` contains `"remind me to "`. This does NOT match because the input is `"remind me 1 minute later"` (no `" to "`). However, the list does NOT need `"remind me "` without `" to "` for this to fail -- it currently passes this check correctly.

**Wait -- re-reading the signals list:**
```python
new_intent_signals = ["remind me to ", "remind me about ", "set a reminder", ...]
```
`"remind me 1 minute later"` starts with `"remind me "` but none of the signals match because they require `"remind me to "` or `"remind me about "`. So this input **does** fall through to the `else` branch at line 421 and gets processed as an alert time via `parse_alert_time` at line 428.

**However**, the problem described in the task prompt says `"remind me"` matches. This means there was likely a prior version with `"remind me "` in the list, or the real breakage is:

- If someone adds `"remind me "` to fix another edge case, THIS breaks.
- The design is one typo away from catastrophic misrouting.
- The signal list is maintained by hand with no tests.

**Verdict:** Currently works by accident. Fragile -- any tightening of the signal list breaks it.

### B. In custom alert flow, types "night before the event"

**Expected:** Parsed as alert time (night before).
**Actual:** Works correctly.

Code path:
1. `handlers.py:360` -- pending exists, `awaiting_custom_alerts` True.
2. `handlers.py:409-414` -- `"night before the event"` does not match any `new_intent_signals`.
3. `handlers.py:421-422` -- not in the `done` words.
4. `handlers.py:428` -- calls `llm.parse_alert_time("night before the event", due_at, tz)`.
5. `llm_service.py:399-487` -- LLM parses it as `absolute` type with date = event_date - 1 day, time = 20:00.

**Verdict:** Works. No issue here.

### C. In custom alert flow, types "remind me to call doctor tomorrow at 3pm"

**Expected:** Recognized as a genuinely new reminder, exits alert flow, creates new reminder.
**Actual:** Works correctly (by design of the signal list).

Code path:
1. `handlers.py:360` -- pending exists, `awaiting_custom_alerts` True.
2. `handlers.py:409-414` -- `"remind me to call doctor tomorrow at 3pm"` starts with `"remind me to "` -- matches!
3. `handlers.py:415-419` -- Clears pending context, sends "Exited alert setup" message.
4. Falls through to `handlers.py:454` -- `classify_intent` runs on the full message.
5. `llm_service.py:185-215` -- LLM classifies as `"reminder"` intent.
6. `handlers.py:491-538` -- `parse_reminder_time` parses it, creates new reminder.

**Verdict:** Works, but only because the exact phrase `"remind me to "` is in the signal list. Edge cases:
- `"remind me call doctor tomorrow at 3pm"` (no "to") -- would be sent to `parse_alert_time` instead.
- `"set reminder for doctor tomorrow at 3pm"` -- would also be sent to `parse_alert_time`.
- `"I need to call doctor tomorrow at 3pm, remind me"` -- sent to `parse_alert_time`.

### D. NOT in any flow, types "remind me couple therapy Monday 4pm"

**Expected:** Creates a new reminder.
**Actual:** Works correctly.

Code path:
1. `handlers.py:360` -- `pending` is None (or no `awaiting_custom_alerts`).
2. Skips both pending blocks (lines 361 and 400).
3. `handlers.py:454-455` -- calls `classify_intent("remind me couple therapy Monday 4pm")`.
4. `llm_service.py:185-215` -- LLM classifies as `"reminder"`.
5. `handlers.py:491-492` -- calls `parse_reminder_time`.
6. `llm_service.py:327-397` -- LLM extracts title + time_phrase, dateparser resolves date.
7. `handlers.py:493-519` -- Creates reminder, asks about alerts.

**Verdict:** Works correctly.

---

## Fragile State Management Issues

### 1. `_pending_reminder_context` is an in-memory dict (line 68)

```python
self._pending_reminder_context: dict[int, dict] = {}
```

- **Lost on restart.** If bot restarts while user is in custom alert flow, the pending state vanishes. User's next message goes to `classify_intent` instead.
- **No expiration.** If user enters custom alert flow and never says "done", pending context persists forever. Any message days later still routes to alert parsing.
- **Single slot per user.** Only one pending context at a time. If user clicks "Custom" on reminder A, then clicks "Add alert" on reminder B (without saying "done"), reminder A's context is silently replaced (line 619-624 / 690-695 do call `_clear_pending_context` first, but the user never sees confirmation that A was abandoned).

### 2. `new_intent_signals` is a hand-curated allowlist (lines 410-413)

```python
new_intent_signals = ["remind me to ", "remind me about ", "set a reminder",
                      "what's the weather", "how's ", "how is ",
                      "/start", "/help", "/status", "/reminders", "/notes",
                      "/briefing", "note:", "note "]
```

Problems:
- **Incomplete.** Misses: `"remind me "` (without "to"/"about"), `"set a reminder for"`, `"don't forget"`, `"can you remind me"`, `"save a note"`, `"check stocks"`, `"what time"`, etc.
- **Includes commands that would never reach this handler.** Lines like `"/start"`, `"/help"` etc. are already filtered by the `~filters.COMMAND` on the MessageHandler (line 147). These signals are dead code.
- **`"note "` is too broad.** User typing `"note: set alert for morning"` would exit the flow.
- **No LLM fallback.** The decision to exit is purely string-based, so it can never understand semantic intent.

### 3. `classify_intent` has no concept of "alert" (llm_service.py:192-201)

The intent classifier only knows these intents:
```
"reminder", "note", "weather", "stock", "news", "general"
```

There is no `"alert"` intent. So even if you tried to use the LLM to decide, it would classify `"remind me 1 minute later"` as `"reminder"` -- which is wrong when the user is adding alerts to an existing reminder. The LLM prompt has zero awareness of the alert flow.

### 4. `parse_alert_time` return value on failure (llm_service.py:460)

```python
            try:
                parsed_list = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("Failed to parse alert time JSON: %s", result)
            return None  # <-- BUG: this return is OUTSIDE the except block
```

Lines 458-460: The `return None` at line 460 is at the same indentation as the outer `try`. After the inner `try/except` at lines 453-459, execution falls through to `return None` unconditionally when the first `json.loads` at line 451 fails -- even if the second `json.loads` at line 456 succeeds. The `parsed_list` from line 456 is never used because line 460 always returns None.

This is an indentation bug. Line 460 should be inside the inner `except` block.

### 5. Pending context set before alert selection (handlers.py:508-514)

When a new reminder is created and `pre_alerts == "ask"`:
```python
self._pending_reminder_context[user_id] = {
    "reminder_id": reminder.id,
    "title": parsed["title"],
    "due_at": due_at_utc,
}
```

This sets pending context but does NOT set `awaiting_custom_alerts: True`. The user is shown alert preference buttons (line 519). If the user types a text message before clicking a button, the pending context exists but has neither `awaiting_reschedule` nor `awaiting_custom_alerts` -- so both pending blocks (lines 361 and 400) are skipped, and the message goes to `classify_intent`. The pending context is silently orphaned.

### 6. No mutex / race condition on pending context

If a user sends two messages rapidly, both could read the same pending state before either clears it. This is unlikely in Telegram but possible.

---

## Summary of Bugs (ordered by severity)

| # | Location | Bug | Impact |
|---|----------|-----|--------|
| 1 | `llm_service.py:460` | `return None` outside `except` block -- alert time parsing always fails on first JSON parse error even if recovery succeeds | Custom alerts silently fail to parse in edge cases |
| 2 | `handlers.py:410-414` | `new_intent_signals` string matching is the wrong abstraction for distinguishing alerts from new intents | False exits from alert flow; missed exits when user wants a new intent |
| 3 | `handlers.py:508-514` | Pending context set without `awaiting_custom_alerts` flag; typing before clicking a button orphans the context | User's alert preference selection silently lost |
| 4 | `llm_service.py:192-201` | No `"alert"` intent in classifier; LLM has no awareness of alert flow state | Even if LLM were used for disambiguation, it would misclassify |
| 5 | `handlers.py:68` | In-memory dict lost on restart, no TTL | Stale/lost state after restart or long gaps |
| 6 | `handlers.py:147` + `handlers.py:412-413` | Command strings in `new_intent_signals` are dead code (`~filters.COMMAND` already excludes them) | Misleading code, false sense of coverage |

---

## Recommended Fix Direction

The root cause is that the code tries to use string matching to solve what is fundamentally a state machine problem. The fix is:

1. **When user is in `awaiting_custom_alerts`, ALL text input goes to `parse_alert_time`. Period.** No `new_intent_signals` check. The only escape hatches should be:
   - Explicit exit words: "done", "no", "cancel", "nevermind"
   - Telegram commands (already handled by `~filters.COMMAND`)
   - A "Cancel" inline button on the alert prompt message

2. **Fix the indentation bug** at `llm_service.py:460`.

3. **Add `awaiting_custom_alerts: True`** to the pending context set at `handlers.py:508-514`, or restructure so the "ask" flow uses buttons only (no text-based state).

4. **Add TTL** to `_pending_reminder_context` entries (e.g., 30 minutes).
