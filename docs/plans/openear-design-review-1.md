VERDICT: NEEDS_REVISION

## Summary Assessment

The design is well-structured for a personal assistant MVP, with sensible technology choices overall. However, there are critical gaps in Gmail OAuth token lifecycle management, security of secrets on a public-facing EC2 instance, and insufficient consideration of Groq free tier rate limits that could silently break core functionality.

## Critical Issues (must fix)

### C1: Gmail OAuth refresh token expiration will silently break email monitoring

Google OAuth refresh tokens issued to apps in "Testing" publishing status expire after 7 days. To get non-expiring refresh tokens, the app must either be pushed to "Production" status (which requires Google's OAuth consent screen verification -- a multi-week review process) or remain in "Testing" with your own Google account explicitly listed as a test user. Even then, Google has been known to revoke refresh tokens under various conditions (password change, 6 months of inactivity on the token, exceeding 50 outstanding refresh tokens per account).

The design has no mention of:
- What publishing status the Google Cloud project will use
- How to detect a stale/revoked token
- How to re-authenticate when it expires (you cannot do the OAuth browser flow from a headless EC2 instance without extra tooling)
- Alerting you via Telegram that email monitoring has silently stopped

**Fix:** Add an explicit section on OAuth token lifecycle. At minimum: (1) detect `google.auth.exceptions.RefreshError` and send a Telegram alert, (2) document whether the app will be in Testing or Production mode, (3) provide a re-authentication path (e.g., a small Flask endpoint temporarily exposed for the OAuth redirect, or a local script that generates the token and you scp it to the server).

### C2: Groq free tier rate limits are restrictive and will be hit by email batches

As of early 2025, Groq's free tier imposes limits in the range of 30 requests per minute and 14,400 requests per day (varying by model). The design calls for per-email LLM classification plus per-email summarization. A morning check that finds 30 unread emails would fire 30 classification calls + N summarization calls in rapid succession, likely hitting the RPM limit.

The design does not mention:
- Any rate-limit awareness or backoff/retry logic
- Batching multiple emails into a single LLM call (which would also reduce token usage)
- What happens when a rate limit is hit mid-briefing (partial briefing? retry? silent failure?)
- A fallback if Groq's free tier changes terms or disappears

**Fix:** (1) Batch emails into a single classification prompt (e.g., "classify these 20 emails as relevant/irrelevant") instead of one-call-per-email. (2) Add exponential backoff with retry on 429 responses. (3) Add a circuit breaker or fallback (e.g., skip LLM classification and use whitelist-only mode when Groq is unavailable). (4) Log and alert on rate limit events.

### C3: API keys and OAuth tokens stored on a public-facing EC2 with no encryption at rest

The design stores TELEGRAM_BOT_TOKEN, GROQ_API_KEY, and Gmail OAuth credentials (which grant full read access to your email) on an EC2 instance accessible from the internet via SSH. The `.env` file and OAuth token JSON sit on disk in plaintext inside the Docker volume.

Specific concerns:
- If the SSH key is compromised or an SSH vulnerability is exploited, the attacker gets full Gmail read access, Telegram bot impersonation, and LLM API access.
- The design does not mention SSH hardening (fail2ban, key-only auth, non-default port, or IP allowlisting in the security group).
- No mention of disk encryption or using AWS Secrets Manager/SSM Parameter Store.
- The SQLite database (also in the Docker volume) will contain email summaries, personal notes, and family information -- all unencrypted.

**Fix:** (1) Restrict the SSH security group to your home/VPN IP range, not 0.0.0.0/0. (2) Use SSH key-only auth (disable password auth) and consider fail2ban. (3) Store secrets in AWS SSM Parameter Store (free tier) or at minimum use EBS encryption. (4) Add this to the design as a "Security" section so it is not an afterthought.

### C4: No health monitoring or crash recovery visibility

The design mentions "auto-restart via systemd" but does not address:
- How you know the bot has crashed and restarted (vs. silently restarting in a loop)
- No health check endpoint or heartbeat mechanism
- No log persistence strategy (Docker logs rotate and disappear; if the container restart-loops, you lose the crash logs)
- No disk space monitoring (SQLite WAL files + Docker layers can fill a t3.micro's 8GB default EBS volume surprisingly fast)
- No memory monitoring (1GB RAM on t3.micro; a large LLM response or email batch loaded into memory could OOM the process)

**Fix:** (1) Add a daily Telegram heartbeat message ("openEar is alive, processed X emails, Y reminders pending"). (2) Mount a Docker volume for logs and configure log rotation. (3) Set up a CloudWatch alarm on disk usage and memory (free tier covers basic monitoring). (4) Add a `/status` command to the Telegram bot that reports uptime, DB size, last email check time, and last error.

## Suggestions (nice to have)

### S1: yfinance is fragile and frequently breaks

yfinance is an unofficial scraper of Yahoo Finance. It has a history of breaking when Yahoo changes their page structure or API, and Yahoo has actively tried to block it in the past. It works today, but it may stop working without warning.

**Suggestion:** Accept the risk for v1 but wrap it in a try/except with a graceful degradation message ("Stock data temporarily unavailable"). Consider Alpha Vantage (free tier, 25 requests/day) as a fallback, or Twelve Data.

### S2: DuckDuckGo scraping for news is unreliable at scale

The design lists "DuckDuckGo (free, no API key)" for web search. The `duckduckgo-search` Python library scrapes DDG's HTML, which DDG can break or rate-limit at any time. It is not an official API.

**Suggestion:** Acceptable for personal use but add error handling. If news lookups are important, consider NewsAPI (free tier, 100 requests/day) as a more reliable source.

### S3: Conversation history pruning to 20 messages loses context silently

The design prunes to the last 20 messages automatically. This means if you have a multi-turn conversation about a complex topic that spans 20+ messages, the bot will silently forget the beginning of the conversation. There is no indication to the user that context has been lost.

**Suggestion:** (1) Add a system message when context is about to be pruned: "Note: older messages in this conversation are no longer in my context." (2) Consider summarizing older messages instead of dropping them (use the LLM to generate a summary of messages 1-10 when message 21 arrives, and keep the summary as a "system" entry).

### S4: The data model lacks timezone storage for reminders

The `reminders` table stores `due_at` as DATETIME but there is no timezone column. The `user_config` table has a timezone key, but if the user travels or changes timezone, existing reminders could fire at the wrong time. SQLite DATETIME is timezone-naive.

**Suggestion:** Store all times as UTC in the database and convert to local time only at display. Add a note in the design about this convention.

### S5: The notes table tags column uses a JSON array in TEXT

Storing tags as a JSON array in a TEXT column means you cannot efficiently query "find all notes with tag X" without scanning every row. This is fine for single-user v1 but will not scale.

**Suggestion:** Fine for now, but note in the "Future Considerations" section that a `note_tags` junction table would be needed for multi-user.

### S6: No backup strategy for the SQLite database

The design mentions "easy backup" as a rationale for SQLite but does not specify an actual backup procedure. The DB is in a Docker volume on a single EC2 instance. If the EBS volume fails, you lose everything.

**Suggestion:** Add a scheduled job (daily cron or APScheduler job) that copies the SQLite file to S3. SQLite's `.backup()` API makes this safe even while the application is running. Cost is negligible on S3.

### S7: Email deduplication relies on gmail_id UNIQUE constraint but no error handling is shown

If the same email is fetched twice (e.g., after a crash mid-processing), the UNIQUE constraint on `gmail_id` will throw an IntegrityError. The design does not mention handling this.

**Suggestion:** Use `INSERT OR IGNORE` / SQLAlchemy's `on_conflict_do_nothing()` for email inserts, or check existence before inserting.

### S8: Quiet hours logic has an edge case at midnight

The quiet hours are defined as `["22:00", "07:00"]` which spans midnight. The implementation needs to handle the "start > end means it wraps around midnight" case correctly. This is a common source of bugs.

**Suggestion:** Call this out in the design or add a code comment. The check is: `if start > end: is_quiet = (now >= start or now < end)`.

### S9: No rate limiting on the Telegram bot side

Anyone who discovers the bot token can send unlimited messages, causing unbounded Groq API calls and potential cost/rate-limit exhaustion.

**Suggestion:** Add a `TELEGRAM_ALLOWED_USER_IDS` config that filters out messages from unauthorized users at the handler level. python-telegram-bot supports this with a simple check on `update.effective_user.id`. This is essential even for single-user.

### S10: Deploy flow has no rollback mechanism

The deploy is `git pull && docker compose up -d --build`. If the new version is broken, there is no documented way to roll back.

**Suggestion:** Tag releases or note the previous commit SHA before deploying. Consider `docker compose up -d --build && docker compose logs -f --tail=50` to verify startup before disconnecting.

## Verified Claims (things you confirmed are correct)

- **python-telegram-bot v20+ is async-native.** Correct. v20 introduced native asyncio support, and it works well with APScheduler's AsyncIOScheduler. Long polling means no inbound ports are needed, which is correct.

- **Telegram Bot API is free with no rate limits for personal use.** Largely correct. Telegram does impose limits (roughly 30 messages/second to different chats, 20 messages/minute to the same chat), but a single-user personal bot will never approach these.

- **Open-Meteo is free and requires no API key.** Correct. Open-Meteo provides free weather data for non-commercial use with no key required. Good choice.

- **SQLite is appropriate for single-user and has a Postgres migration path.** Correct, especially with SQLAlchemy as the ORM layer. The migration path is real -- SQLAlchemy abstracts the dialect, and Alembic can manage schema migrations.

- **EC2 t3.micro is free-tier eligible.** Correct for the first 12 months of a new AWS account (750 hours/month). After that, it costs roughly $7.50/month. The design should note the free tier is time-limited.

- **Long polling eliminates the need for SSL certs and inbound ports.** Correct. Webhook mode requires HTTPS and an open port; long polling does not. Good architectural call for simplicity.

- **APScheduler works in-process with asyncio.** Correct. `APScheduler` with `AsyncIOScheduler` runs within the same event loop as python-telegram-bot. No external scheduler (cron, celery) is needed.

- **The hybrid email filtering approach (whitelist + LLM fallback) is sound.** This is a good design. Whitelisted senders skip the LLM call entirely (faster, cheaper), while unknown senders get LLM classification. This naturally reduces API usage.

- **Docker volume persistence for SQLite is correct.** Mapping `/data/openear.db` to a Docker volume ensures the DB survives container rebuilds. This is the right approach.
