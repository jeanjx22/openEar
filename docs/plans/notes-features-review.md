VERDICT: NEEDS_REVISION

## Review: Enhanced Notes/Logs Features -- Implementation Plan

Reviewer: IC6 engineer
Date: 2026-04-24

---

### Overall Assessment

The plan is well-structured and demonstrates good decomposition. The dependency table and parallel execution groups are clear. However, there are several issues ranging from a significant design concern with Feature 1 (auto-log) to missing pieces in Feature 4 and minor correctness problems throughout.

---

### 1. Are tasks truly 2-5 minutes each?

Most tasks are appropriately scoped. Three exceptions:

- **Task 9** (activity_log + search_activity handlers + _parse_time_range helper) is three changes in one: two intent handlers and a new helper method. This is closer to 8-10 minutes. Split the `_parse_time_range` helper into its own task, or at least acknowledge the combined scope.

- **Task 11** spans three files (handlers.py x2 locations, keyboards.py) with three sub-tasks (11a, 11b, 11c). The sub-task notation is good but the aggregate is 7-10 minutes. The plan correctly labels these as sub-tasks, so this is acceptable if the executor treats them as a single sitting.

- **Task 12** (integration test + rules.yaml) is fine as-is since it is mostly manual verification.

---

### 2. Feature 1 (Auto-log) -- spam risk

**This is the most significant concern in the plan.**

The auto-save design fires an LLM call on every general conversation turn (Task 7 wires it into the `else` branch of `_process_single_intent`). Problems:

**(a) LLM cost and latency.** Every general conversation message now incurs two LLM calls: one for `chat()` and one for `should_auto_save()`. This doubles the Groq API usage for the most common code path. The circuit breaker check in `_maybe_auto_save` only skips when already tripped -- it does not prevent the call volume from tripping the breaker in the first place.

**(b) Spam risk.** The "Noted that!" confirmation (Task 10, line 444) will feel intrusive during normal conversation. If the user says "Aaron's teacher is Ms. Johnson and she's really nice and also his art teacher is Mr. Park," the LLM may save multiple times across conversation turns or miss deduplication against previously saved notes. There is no duplicate detection -- `save_note` always inserts.

**(c) No user opt-out.** There is no way to disable auto-save or review what was auto-saved. The `rules.yaml` addition in Task 12a adds `auto_save.enabled: true` but no code reads that flag. The `_maybe_auto_save` method does not check `self.settings.rules`.

**Required changes:**

1. Add a config check at the top of `_maybe_auto_save`: `if not self.settings.rules.get("notes", {}).get("auto_save", {}).get("enabled", False): return`
2. Change the confirmation from the chatty `"Noted that!"` to a silent save, or at most a subtle indicator appended to the main response. A separate message after every auto-save will annoy users.
3. Add basic deduplication: before saving, call `self.notes.search_notes(content)` and skip if a sufficiently similar note already exists.
4. Consider rate-limiting: at most one auto-save per conversation session or per N messages.

---

### 3. Can Features 2-4 reuse existing code?

**(a) Feature 2 (Search Notes):** Good reuse. Task 3 correctly calls `self.notes.search_notes(content)` and `self.notes.search_by_tag(tag)`, both of which already exist in `NoteService`. The deduplication by `note.id` is correct. No issues.

**(b) Feature 3 (Activity Log):** The new `get_notes_by_tag_since` method (Task 4) largely duplicates `search_by_tag` with an added date filter. Consider refactoring: add an optional `since` parameter to the existing `search_by_tag` method instead of creating a near-identical method. This keeps the API surface smaller and avoids the pattern where two methods with 90% identical code drift apart over time.

**(c) Feature 4 (Note-to-Reminder):** Task 11 reuses `keyboards.note_followup` and the existing `note_remind:` callback handler (line 1403 in handlers.py). However, the callback handler at line 1403-1408 only asks "when should I remind you?" -- it does not actually create the reminder. There is no `AWAITING` state transition or follow-up handler for the user's time response. This is an existing gap, but the plan should acknowledge it. The new `note_remind_with_context` keyboard uses the same `note_remind:{note_id}` callback_data, so it will correctly hit the same handler -- good.

---

### 4. Is the Note model sufficient?

The existing `Note` model has: `id`, `content`, `tags` (JSON text), `created_at`.

**This is sufficient for all four features.** The activity log uses tags (`["activity_log", "husband"]`) to distinguish activity entries from regular notes, which is a clean approach that avoids schema migration. The `search_notes` content search and `search_by_tag` tag search cover the query patterns.

**No new columns are needed.** However, one consideration for later: if the activity log grows large, the `LIKE`-based queries on `tags` and `content` columns will degrade. An index on `created_at` would help `get_notes_by_tag_since`. Not blocking, but worth a future TODO.

---

### 5. Any conflicts between features?

**(a) Auto-save vs. activity log intent overlap.** If a user says "husband went to tennis" in general conversation (not recognized as `activity_log` intent), the auto-save in Feature 1 might save it as a generic note with tag `["auto_saved"]` instead of `["activity_log", "husband"]`. Then the activity search in Feature 3 would miss it. This is an intent classification boundary issue -- the fix is in the LLM prompt, not the code, but the plan should acknowledge the edge case.

**(b) Auto-save vs. explicit note save.** If a user says "Aaron is allergic to peanuts" and the intent classifier routes it to `"note"` (explicit save), then the auto-save check in the `else` branch never runs -- no conflict. But if the classifier routes it to `"general"`, the auto-save fires. This is correct behavior. No conflict.

**(c) `note_remind_with_context` vs. `note_followup`.** Task 11a replaces the existing `note_followup` keyboard with `note_remind_with_context` for future-event notes, and preserves `note_followup` for recurring-pattern notes. Both use the same `note_remind:{note_id}` callback_data format. No conflict.

**(d) `search_notes` vs. `search_activity` intent.** A query like "what did I note about husband's tennis?" could match either `search_notes` or `search_activity`. The LLM classifier needs clear disambiguation instructions. The plan's prompt additions are adequate but could be tighter -- add an explicit note that `search_activity` is specifically for "what did [person] do" queries while `search_notes` is for "what did I note about [topic]" queries.

---

### 6. Is the plan ordered correctly for dependencies?

The dependency table is mostly correct. Two issues:

**(a) Task 9b is missing from the dependency table.** It is listed as a task in the Feature 3 section but does not appear in the table at lines 9-24. Task 9 (the handler) calls `formatters.format_activity_log`, which is defined in Task 9b. So Task 9 depends on Task 9b, or they must be executed together. Add Task 9b to the table with dependency on Task 2 (same as Task 9) or merge it into Task 9.

**(b) Task 3 depends on Task 6.** Task 3 (search_notes handler) calls `formatters.format_note_search_results`, which is defined in Task 6. The dependency table says Task 3 depends only on Task 1, but it also depends on Task 6 (or vice versa -- Task 6 should come before Task 3, or they should be reordered). Currently the table has Task 6 depending on Tasks 1 and 3, which creates a circular dependency: Task 3 calls a function from Task 6, but Task 6 depends on Task 3. Fix: Task 6 should depend only on Task 1 (not Task 3), and Task 3 should depend on Tasks 1 and 6.

---

### 7. Additional Issues

**(a) Missing import in Task 4.** The plan says to add `from datetime import datetime` to `note_service.py`. But `datetime` is not imported from `datetime` at module level -- the file uses `from __future__ import annotations` (line 1) which makes the `datetime` type annotation a string. The actual runtime use of `datetime` in the `since` comparison will work because SQLAlchemy handles the comparison. However, the type hint `datetime | None` in the method signature is only valid at runtime if `datetime` is imported. With `from __future__ import annotations`, it becomes a string annotation and does not need the import at runtime. But the `isinstance` or comparison `Note.created_at >= since` uses the value, not the type, so no runtime import is needed for that. The import is still good practice for clarity. Minor: confirm that adding `from datetime import datetime` does not shadow any existing usage. Looking at the current imports, `datetime` is not imported -- only `json`, `logging`, `select`, `get_session`, and `Note`. The import is safe.

**(b) Task 10 missing error handling for LLM parse failure.** The `_maybe_auto_save` method catches `Exception` broadly (good), but the `result.get("should_save")` path does not validate that `content` is non-empty. If the LLM returns `{"should_save": true, "content": "", "tags": []}`, it saves an empty note. Add: `if not content: return`.

**(c) Task 11b duplicates logic with cmd_save_note.** After this plan is implemented, there will be two places that save notes with a future-event check: `cmd_save_note` (Task 11a) and the `"note"` intent handler (Task 11b). If the future-event logic ever changes, both must be updated. Consider extracting a shared `_save_note_with_event_check` helper method.

---

### Summary of Required Changes

| # | Severity | Description |
|---|----------|-------------|
| 1 | **Blocking** | Auto-save must check `rules.yaml` `auto_save.enabled` flag before running |
| 2 | **Blocking** | Fix circular dependency between Tasks 3 and 6 in the dependency table |
| 3 | **High** | Auto-save confirmation message should be silent or appended, not a separate message |
| 4 | **High** | Add deduplication check before auto-saving |
| 5 | **Medium** | Add Task 9b to the dependency table |
| 6 | **Medium** | Consider refactoring `get_notes_by_tag_since` as an extension of `search_by_tag` |
| 7 | **Medium** | Add empty-content guard in `_maybe_auto_save` |
| 8 | **Low** | Extract shared note-save-with-event-check helper to avoid duplication in 11a/11b |
| 9 | **Low** | Document intent overlap edge case between auto-save and activity_log |

Fix items 1-4, then the plan is approved.
