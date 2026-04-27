VERDICT: NEEDS_REVISION

# Features 2-6 Design Review

Reviewer context: read `jobs.py`, `handlers.py`, `llm_service.py`, `config.py`, `info_service.py`, `reminder_service.py`, and `rules.yaml` in full.

---

## 1. Completeness -- missing pieces

### Feature 2 (Setup Command)

- **No schema validation.** If the LLM produces a bad rules.yaml mutation (wrong key, wrong type, nonsensical cron time), nothing catches it. The plan needs an explicit validation step between LLM output and YAML write. At minimum: validate check_times are parseable "HH:MM", validate stock_symbols are strings, validate booleans are booleans.
- **No confirmation step.** "Check emails at 7am and 9:30pm" should show the user what will change and get a "yes" before rewriting config. Without this, a misclassified intent could silently overwrite real settings.
- **No audit trail.** If the user says "what were my old email times?" after a change, there is no way to answer. At minimum log the before/after diff; ideally keep a `config_history` table.

### Feature 4 (Morning Stock Briefing)

- **No error message in the briefing itself.** If one or more stock fetches fail, the plan does not specify what the user sees. It should degrade per-symbol ("NVDA: unavailable") rather than silently omitting or crashing the entire briefing.
- **Stale market data on holidays/weekends.** `weekdays_only: true` gates whether stocks are included, but the plan does not mention what happens if a US market holiday falls on a weekday. Minor, but worth a comment in the code.

### Feature 5 (Sunday Night Week-Ahead)

- **No definition of "Sunday evening".** Which of the configured `check_times` counts as Sunday evening? If check_times are ["07:00", "21:30"], does "Sunday evening" mean the 21:30 run on Sunday? This needs to be explicit.

### Feature 6 (Evening Briefing)

- **Missing "no upcoming items" case.** If `get_future_reminders()` returns nothing for the next 7 days, the plan does not say whether to send "Nothing upcoming this week" or skip the section entirely.

---

## 2. Overengineering

### Feature 2 is too ambitious as stated

Writing to `rules.yaml` at runtime, hot-reloading it, and rescheduling cron jobs is a lot of machinery for a personal bot. The simpler path:

1. Store user preferences in a `settings` DB table (key-value or JSON blob).
2. On bot startup, merge DB settings over `rules.yaml` defaults.
3. Cron jobs read from `self.settings` (already in memory) -- no file I/O, no reload, no race conditions.
4. For schedule changes specifically, use `scheduler.reschedule_job()` which APScheduler already supports without needing to tear down and rebuild anything.

This eliminates the entire "hot-reload YAML" concern (see Section 6 below) and is safer for a single-user bot.

### Features 5 and 6 overlap (see Section 3)

Treating them as separate features is overengineering. They are the same thing: "append upcoming reminders to the briefing."

---

## 3. Feature conflicts -- Features 5 and 6 overlap heavily

Feature 5 says: "Sunday night, query `get_future_reminders()`, filter to next 7 days."

Feature 6 says: "Every evening, include reminders due within next 7 days. Sunday gets extra detail."

These produce the same output on Sunday evening. On other evenings, Feature 6 already covers the next 7 days. Feature 5 adds no incremental value.

**Recommendation:** Merge them into one behavior:

- Every evening briefing appends "Upcoming this week" showing reminders from `get_future_reminders()` filtered to the next 7 days.
- On Sunday, optionally add a header ("Week Ahead Preview") and slightly richer formatting (e.g., group by day).
- This is one code path with a `calendar.day_name[now.weekday()] == "Sunday"` branch for formatting, not two separate features.

---

## 4. How does `_email_briefing_job` know morning vs. evening vs. Sunday?

This is the most significant gap in the plan. Today, `_email_briefing_job` is a single function called by multiple CronTrigger jobs. It receives no arguments telling it which trigger fired.

**Current code (jobs.py lines 119-129):**
```python
for check_time in check_times:
    hour, minute = map(int, check_time.split(":"))
    self.scheduler.add_job(
        self._email_briefing_job,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        ...
    )
```

There is no way for the job to know if it is the 07:00 or 21:30 invocation. The plan needs to address this. Two options:

**Option A (recommended): pass the check_time as an argument.**

```python
self.scheduler.add_job(
    self._email_briefing_job,
    CronTrigger(hour=hour, minute=minute, timezone=tz),
    kwargs={"check_time": check_time},
    ...
)
```

Then inside the job:

```python
async def _email_briefing_job(self, check_time: str = "") -> None:
    rules = self.settings.rules.get("briefing", {})
    is_morning = check_time == check_times[0]  # or compare hour < 12
    is_evening = not is_morning
    is_sunday = datetime.now(ZoneInfo(tz)).weekday() == 6
```

**Option B: compare current time against rules.** Less clean -- the job computes "am I morning or evening?" from `datetime.now()`. This is fragile if the job fires a few minutes late due to `misfire_grace_time=3600`.

Option A is straightforward and costs about 5 lines of code.

---

## 5. Stock fetching latency

`get_stock_quote` calls `yfinance` via `asyncio.to_thread()`. Each call is an HTTP round-trip. For 6 symbols (the current `stock_symbols` list), running them sequentially means 6 serial HTTP calls, each potentially 2-5 seconds. That is 12-30 seconds of delay before the email briefing is delivered.

**Recommendations:**

1. **Fetch all stocks concurrently** using `asyncio.gather()`:
   ```python
   quotes = await asyncio.gather(
       *[get_stock_quote(sym) for sym in stock_symbols],
       return_exceptions=True,
   )
   ```
   This brings latency down to the single slowest call rather than the sum.

2. **Set a per-symbol timeout** (e.g., 10 seconds) so one hung symbol does not block the briefing:
   ```python
   async def _fetch_with_timeout(sym, timeout=10):
       try:
           return await asyncio.wait_for(get_stock_quote(sym), timeout=timeout)
       except asyncio.TimeoutError:
           return f"{sym}: timed out"
   ```

3. **Send the email briefing first, stocks second.** The email summary is the high-priority content. Send it immediately, then send stocks as a separate follow-up message. This way the user sees their emails without waiting for Yahoo Finance. This also simplifies error handling -- if all stocks fail, the email briefing is unaffected.

---

## 6. Hot-reloading rules.yaml and rescheduling -- safety concerns

The plan says Feature 2 will "update rules.yaml and restart relevant scheduler jobs without bot restart." There are several problems:

### 6a. Race condition: concurrent reads and writes

`_email_briefing_job` reads `self.settings.rules` while the setup handler writes to `rules.yaml` and reloads. If a briefing job fires at exactly 07:00 while the user is changing settings at 06:59:59, the job may read a partially-updated `self.settings.rules` dict. Python's GIL does not protect against this because the reload involves file I/O and dict reconstruction across multiple statements.

### 6b. File write atomicity

`yaml.dump()` to an open file is not atomic. A crash mid-write produces a corrupt `rules.yaml`. The bot will not start again until the file is manually fixed. For a personal bot running on a remote server, this is a real availability risk.

### 6c. Settings object is passed by reference everywhere

`Settings` is created once in `load_settings()` and passed to `SchedulerJobs`, `BotHandlers`, `ReminderService`, and `LLMService`. Replacing `self.settings.rules` in one place does not update the `ReminderService` instance's snapshot of quiet hours (`self._quiet_start`, `self._quiet_end`), which are computed once in `__init__`. Hot-reload would require every service to re-read its config, or use a pattern like `self.settings.rules["reminders"]["quiet_hours"]` on every access instead of caching in `__init__`.

### Recommendation

Do not write to rules.yaml at runtime. Use a DB table for mutable user preferences (see Section 2). This eliminates all three problems:

- DB writes are atomic (SQLite WAL mode).
- No file corruption risk.
- Services read from the DB or from `self.settings` which can be updated in a single dict assignment.

For cron schedule changes specifically, `APScheduler.reschedule_job(job_id, trigger=new_trigger)` is the correct API. It is atomic and thread-safe.

---

## 7. DB vs. rules.yaml for Feature 2

**Strongly recommend DB over YAML** for the following reasons:

| Concern | rules.yaml | DB table |
|---|---|---|
| Atomicity | No (file write can corrupt) | Yes (SQLite transaction) |
| Concurrent access | Unsafe | Safe (SQLite WAL) |
| Audit trail | Requires manual diffing | Can add `updated_at`, `previous_value` columns |
| Backup | Separate from DB backup | Included in existing S3 backup job |
| Restart required | Yes (or build complex reload) | No |
| Migration | Edit file on server | Standard DB migration |

**Proposed schema:**

```sql
CREATE TABLE user_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,        -- JSON-encoded
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Startup behavior:** `load_settings()` reads `rules.yaml` for defaults, then overlays any rows from `user_settings`. This keeps `rules.yaml` as the static default and makes all runtime changes go through the DB.

**For schedule changes:** When the user says "check emails at 6:30am and 9:30pm", the handler:

1. Validates the parsed times.
2. Writes `{"key": "email.check_times", "value": "[\"06:30\", \"21:30\"]"}` to `user_settings`.
3. Updates `self.settings.rules["email"]["check_times"]` in memory.
4. Calls `scheduler.reschedule_job("email_check_07:00", trigger=CronTrigger(hour=6, minute=30, ...))` (or removes old jobs and adds new ones).
5. Confirms to the user.

This is safe, atomic, and does not require any hot-reload machinery.

---

## Summary of required changes before approval

1. **Merge Features 5 and 6** into a single "evening briefing enrichment" behavior with a Sunday formatting branch.
2. **Pass `check_time` as a kwarg** to `_email_briefing_job` so the job knows whether it is a morning or evening run.
3. **Use a DB table** for mutable settings instead of writing to `rules.yaml` at runtime. This resolves the race condition, atomicity, and reload concerns.
4. **Fetch stocks concurrently** with `asyncio.gather()` and per-symbol timeouts, or send stocks as a separate message after the email briefing.
5. **Add a confirmation step** to Feature 2 before applying config changes.
6. **Add schema validation** for LLM-parsed config values before persisting them.
