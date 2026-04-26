# Evaluation: Alert State Machine Redesign

## 1. Bug Coverage

| Bug | Fixed? | Notes |
|-----|--------|-------|
| #1 `return None` indentation in `llm_service.py:460` | No | Redesign doc explicitly says "No Changes Required" to `llm_service.py`. This bug must still be fixed separately. |
| #2 `new_intent_signals` regex misrouting | Yes | Eliminated entirely. All text in `AWAITING_ALERT_TIME` goes to `parse_alert_time`. |
| #3 Pending context set without `awaiting_custom_alerts` | Yes | `UserState` always has an explicit `mode`. No more ambiguous half-set dicts. |
| #4 No "alert" intent in classifier | Sidestepped | No longer relevant since state-based routing bypasses the classifier entirely. Correct approach. |
| #5 In-memory state lost on restart, no TTL | No | `_user_states` is still an in-memory dict. The debug analysis recommended TTL; the redesign adds none. |
| #6 Dead command strings in signal list | Yes | Signal list deleted entirely. |

**Verdict: 4/6 fixed. Bug #1 (the indentation bug) is a one-line fix that should be listed in the migration checklist. Bug #5 (TTL/persistence) is acknowledged but deferred -- acceptable for a personal bot, but the doc should say so explicitly.**

## 2. State Machine Completeness

The three states (IDLE, AWAITING_ALERT_TIME, AWAITING_RESCHEDULE) cover all current flows. No missing states.

One missing transition: the `email_remind:` callback (line 760 in current code) sets pending context with no mode flag. The redesign does not mention it. This should become a third mode or be converted to a button-only flow.

## 3. Edge Cases

- **Photo/sticker in AWAITING_ALERT_TIME**: The `MessageHandler` filter is `filters.TEXT & ~filters.COMMAND`, so non-text messages are silently ignored. This is fine -- no state corruption -- but the user gets no feedback. Consider adding a catch-all handler that replies "Send a time or say 'done'."
- **Bot restart in AWAITING_ALERT_TIME**: State is lost. User's next message goes to `classify_intent`. Not great, but acceptable for a personal bot. The user will simply re-enter the flow.
- **Group chat, two users**: `_user_states` is keyed by `user_id`, so each user has independent state. Correct.
- **Two users in AWAITING_ALERT_TIME simultaneously**: Same answer -- independent state per user. No conflict.

## 4. Is the Disambiguate Keyboard Necessary?

Yes, and this is the strongest part of the design. The alternative -- having the LLM return a "this is not an alert time" flag -- reintroduces the exact problem being solved: an automated system guessing user intent from message content. The keyboard makes the user the disambiguator, which is always correct. Keep it.

One suggestion: add a timeout. If the user ignores the keyboard and sends another message, that message should also go to `parse_alert_time` (current design does this since state stays in AWAITING_ALERT_TIME). Good.

## 5. Complexity

This is not overengineered. The `UserMode` enum + `UserState` dataclass replaces an untyped dict with boolean flags -- it is simpler to reason about than the current code. The three-way dispatch in `handle_message` is clearer than the nested `if pending and pending.get(...)` blocks. The new method extraction (`_handle_alert_time_input`, `_handle_reschedule_input`, `_handle_idle_message`) improves readability without adding abstraction layers.

## 6. Migration / Test Impact

No DB schema changes. The public interface (`handle_message`, `callback_handler`) is unchanged. Tests that mock `_pending_reminder_context` will need to switch to `_user_states`, but the behavior is the same. Tests that send messages and check replies should pass with minimal changes. Estimate: ~10-15 tests need dict-to-dataclass updates; the other 75+ should pass untouched.

## Recommendation

**Approve with two required additions before implementation:**

1. Fix the `llm_service.py:460` indentation bug (add to migration checklist as step 0).
2. Add a one-line note that TTL on `_user_states` is intentionally deferred, with a TODO for later.
