# openEar -- Personal Assistant Design (v2)

## Changes from previous version

This revision addresses all critical issues and selected suggestions from the [design review](openear-design-review-1.md).

### Critical fixes

| ID | Issue | What changed |
|----|-------|--------------|
| C1 | Gmail OAuth token lifecycle | Added "Gmail OAuth Token Lifecycle" section: detect `RefreshError`, alert via Telegram, re-auth path via temporary Flask endpoint, document Testing-mode implications |
| C2 | Groq rate limits | Added "Groq Rate Limit Handling" section: batch classification prompts, exponential backoff with retry on 429, circuit breaker that falls back to whitelist-only mode, rate limit event logging and alerting |
| C3 | Security gaps | Added "Security" section: SSH hardening (key-only, fail2ban, non-default port), security group restricted to home IP, AWS SSM Parameter Store for secrets, EBS encryption |
| C4 | Health monitoring | Added "Health Monitoring" section: daily Telegram heartbeat, `/status` bot command, Docker volume log persistence with rotation, CloudWatch alarms for disk and memory |

### Incorporated suggestions

| ID | Suggestion | What changed |
|----|-----------|--------------|
| S1 | yfinance fragile | Info Services section now wraps yfinance in try/except with graceful degradation message; Alpha Vantage noted as future fallback |
| S3 | Conversation context loss | Conversation pruning now summarizes older messages via LLM instead of dropping them; bot notifies user when summarization occurs |
| S4 | Timezone storage | All DATETIME columns now documented as UTC; display-time conversion happens at the bot layer only |
| S5 | Notes tags column | Added note in Future Considerations about `note_tags` junction table for multi-user |
| S6 | SQLite backup | Added daily S3 backup via APScheduler job using SQLite `.backup()` API |
| S7 | Email dedup error handling | Specified `INSERT OR IGNORE` / `on_conflict_do_nothing()` for email inserts |
| S8 | Quiet hours midnight edge case | Added explicit wrap-around logic note in Reminders flow |
| S9 | Telegram user filtering | Added `TELEGRAM_ALLOWED_USER_IDS` enforcement at handler level; unauthorized messages are silently dropped |
| S10 | Deploy rollback | Deploy flow now tags the previous commit SHA before pulling; includes post-deploy log verification step |

### Not incorporated (with rationale)

| ID | Suggestion | Rationale |
|----|-----------|-----------|
| S2 | DuckDuckGo reliability | Accepted risk for v1 personal use; error handling already covered by general try/except in info services. NewsAPI noted in Future Considerations. |

---

## Overview

openEar is a personal AI assistant that communicates via Telegram, monitors Gmail for important emails (school, medical), sends scheduled briefings, manages reminders and notes, and handles general conversation and info lookups -- all powered by Groq LLM inference.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Messaging | Telegram Bot | Free API, rich inline keyboards, zero rate-limit friction |
| Hosting | AWS EC2 (t3.micro, Ubuntu 24.04) | Always-on for scheduled tasks, free tier eligible (first 12 months; ~$7.50/month after) |
| Language | Python 3.12 (async) | Best ecosystem for Gmail, Telegram, Groq SDKs |
| Framework | python-telegram-bot v20+, APScheduler, SQLAlchemy | Mature async libraries, in-process scheduling |
| Database | SQLite | Zero ops for single-user, easy backup, Postgres migration path |
| LLM | Groq API (Llama 3 / Mixtral) | Fast inference, free tier generous for personal use |
| Email filter | Hybrid (whitelist + LLM classification) | Catches known senders fast, LLM catches new ones |
| Reminder UX | Inline keyboards + natural language | Quick taps for structured actions, free-form for ad-hoc |
| Web search | DuckDuckGo (free, no API key) | News/context enrichment, lightweight lookups |
| Weather | Open-Meteo (free, no API key) | Reliable, unlimited calls |
| Stocks | yfinance (free, no API key) | Real-time-ish quotes, zero setup; wrapped with graceful degradation |
| Deployment | Docker Compose on EC2 | Reproducible, one-command deploy, volume-persisted DB |
| Source control | GitHub (private repo, jeanjx22) | Version history, deploy pipeline, public launch path |
| Languages | English + Chinese | LLM handles both natively, responds in user's language |
| Email summary language | Same as source email | Preserves fidelity, user can ask for re-summary in other language |
| Secrets management | AWS SSM Parameter Store | Free tier, encrypted at rest, no plaintext .env on disk |
| Datetime storage | UTC everywhere | All DB columns store UTC; conversion to local time at display layer only |
| Telegram auth | User ID allowlist | Prevents unauthorized access even if bot token leaks |

## Architecture

```
+---------------------------------------------------------+
|                      Telegram Bot                       |
|  +----------+-----------+--------+--------+-----------+ |
|  |Briefing  | Reminder  | Notes  |  Chat  |  Tools    | |
|  |Handler   | Handler   |Handler |Handler |  Router   | |
|  +----+-----+-----+-----+---+----+---+----+-----+----+ |
|       v           v         v        v           v      |
|  +--------+ +--------+ +-------+ +------+ +---------+  |
|  | Email  | |Schedule| |Storage| | LLM  | |  Info   |  |
|  |Service | |Service | | Layer | |Service| |Services |  |
|  |(Gmail) | |(APSch) | |(SQLite)| |(Groq)| |News/Wx/ |  |
|  |        | |        | |       | |      | | Stocks  |  |
|  +--------+ +--------+ +-------+ +------+ +---------+  |
|       |                     |                           |
|       v                     v                           |
|  +-----------+        +-----------+                     |
|  |  OAuth    |        |  S3       |                     |
|  |  Monitor  |        |  Backup   |                     |
|  +-----------+        +-----------+                     |
|                                                         |
|  +----------------------------------------------------+ |
|  | Auth Guard: TELEGRAM_ALLOWED_USER_IDS filter       | |
|  +----------------------------------------------------+ |
|  +----------------------------------------------------+ |
|  | Health: heartbeat, /status, CloudWatch, log volume | |
|  +----------------------------------------------------+ |
+---------------------------------------------------------+
```

Single async Python process. No microservices. Telegram bot uses long polling (no inbound ports needed). Scheduler, bot, and services share memory in-process. All incoming Telegram messages are filtered by user ID before reaching any handler.

## Security

### SSH hardening

- Key-only authentication; password auth disabled (`PasswordAuthentication no` in sshd_config)
- Non-default SSH port (e.g., 2222) to reduce drive-by scanning
- fail2ban installed and configured for SSH
- Security group inbound rules: SSH restricted to home/VPN IP CIDR only (not 0.0.0.0/0), no other inbound ports

### Secrets management

All secrets are stored in AWS SSM Parameter Store (free tier, encrypted with AWS-managed KMS key):
- `TELEGRAM_BOT_TOKEN`
- `GROQ_API_KEY`
- Gmail OAuth refresh token
- `TELEGRAM_ALLOWED_USER_IDS`

The application fetches secrets from SSM at startup via `boto3`. No `.env` file with secrets exists on disk. The `.env` file contains only non-sensitive config (region, log level, timezone).

### EBS encryption

The EBS volume attached to the EC2 instance uses AWS-managed encryption at rest. This protects the SQLite database (which contains email summaries, personal notes, and family information) and any cached OAuth tokens.

### Telegram user ID filtering

A `TELEGRAM_ALLOWED_USER_IDS` configuration (comma-separated list of numeric Telegram user IDs) is checked at the top of every handler. Messages from unauthorized users are silently dropped -- no response, no LLM call, no logging of message content (only the rejected user ID is logged).

```python
# Applied as the outermost filter in every handler group
async def auth_filter(update: Update, context: CallbackContext) -> bool:
    allowed = context.bot_data.get("allowed_user_ids", set())
    if update.effective_user and update.effective_user.id not in allowed:
        logger.warning("Rejected message from user_id=%s", update.effective_user.id)
        return False  # drop silently
    return True
```

## Gmail OAuth Token Lifecycle

### Publishing status

The Google Cloud project will use **Testing** publishing status with the owner's Google account listed as a test user. This avoids the multi-week OAuth consent screen verification process. The tradeoff is that refresh tokens technically expire after 7 days for non-test-user accounts, but for the owner's own account listed as a test user, tokens persist indefinitely under normal conditions.

### Token revocation scenarios

Refresh tokens can still be revoked by:
1. User password change
2. 6 months of token inactivity
3. Exceeding 50 outstanding refresh tokens per account
4. Manual revocation in Google Account security settings

### Detection

The email service wraps every Gmail API call with error handling:

```python
try:
    messages = gmail_service.users().messages().list(...).execute()
except google.auth.exceptions.RefreshError as e:
    await alert_token_expired(e)
    raise EmailServiceUnavailable("OAuth token expired or revoked")
except google.auth.exceptions.TransportError as e:
    # Transient network issue -- retry with backoff
    ...
```

When a `RefreshError` is caught:
1. Email monitoring is paused (no further Gmail API calls attempted)
2. A high-priority Telegram alert is sent to the user with the error details and re-auth instructions
3. The alert is repeated daily until resolved
4. The `/status` command reflects "Email: DISCONNECTED -- OAuth token expired"

### Re-authentication path

Since the EC2 instance is headless, the OAuth browser flow cannot run directly on it. Two options are provided:

**Option A -- Temporary Flask endpoint (primary):**
1. A management script (`scripts/reauth_gmail.py`) starts a temporary Flask server on the EC2 instance on a random high port
2. The security group is temporarily updated to allow inbound on that port from the user's IP
3. The user opens the URL in a browser, completes the Google OAuth flow
4. The new refresh token is saved to SSM Parameter Store
5. The Flask server shuts down and the security group rule is removed
6. The bot is notified to resume email monitoring

**Option B -- Local generation + upload (fallback):**
1. Run `scripts/setup_gmail.py` locally on a machine with a browser
2. Copy the generated token to SSM Parameter Store via `aws ssm put-parameter`
3. Restart the bot container or send a `/reload_token` command

## Groq Rate Limit Handling

### Batched classification

Instead of one LLM call per email, the email service batches up to 10 emails into a single classification prompt:

```
Classify each of the following emails as RELEVANT or IRRELEVANT
based on these criteria: [school, medical, urgent, family].
Return a JSON array with the same order.

1. From: teacher@school.edu | Subject: Field trip permission slip
2. From: newsletter@store.com | Subject: 50% off weekend sale
...
```

This reduces a 30-email morning check from 30+ API calls to 3 classification calls + N summarization calls (only for relevant emails).

### Exponential backoff with retry

All Groq API calls go through a wrapper that handles rate limiting:

```python
@retry(
    retry=retry_if_exception_type(groq.RateLimitError),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def call_groq(self, messages, model="llama3-70b-8192"):
    ...
```

On 429 responses: wait 2s, 4s, 8s, 16s, up to 60s max, for 5 attempts.

### Circuit breaker

If Groq returns 3 consecutive rate limit errors within a 5-minute window (after retries are exhausted), the circuit breaker trips:

1. LLM classification is disabled; only whitelist-based filtering is used
2. Email summarization falls back to a simple subject + first 200 chars of body
3. General chat responds with "I am temporarily unable to process complex requests. Please try again in a few minutes."
4. A Telegram alert is sent: "Groq rate limit hit -- operating in whitelist-only mode"
5. The circuit breaker resets after 10 minutes and attempts one probe call
6. If the probe succeeds, full LLM functionality is restored; if not, the breaker re-trips

### Rate limit logging

All rate limit events (429 responses, retries, circuit breaker state changes) are logged at WARNING level with timestamps. The `/status` command includes a "Groq: OK / RATE_LIMITED / CIRCUIT_OPEN" indicator and the count of 429s in the last 24 hours.

## Health Monitoring

### Daily heartbeat

An APScheduler job runs at 08:00 local time and sends a Telegram message:

```
openEar health report:
- Uptime: 3d 14h 22m
- Emails processed today: 0 (next check: 07:00)
- Emails processed yesterday: 12
- Pending reminders: 3
- DB size: 4.2 MB
- Disk free: 3.1 GB / 8 GB
- Memory: 210 MB / 1024 MB
- Groq status: OK (0 rate limits in 24h)
- Gmail status: Connected
- Last error: none
```

### /status command

The bot responds to `/status` with a real-time version of the heartbeat data plus:
- Container uptime (from process start time)
- Last successful email check timestamp
- Last successful Groq API call timestamp
- Current circuit breaker state
- S3 backup: last successful backup timestamp and size

### Log persistence

Docker Compose mounts a named volume at `/var/log/openear/` inside the container. The application uses Python's `RotatingFileHandler`:
- Max file size: 10 MB
- Keep 5 rotated files (50 MB total max)
- Log level: INFO for general, WARNING for rate limits and errors

Docker's own logging driver is set to `json-file` with `max-size: 10m` and `max-file: 3` to prevent Docker log accumulation.

### CloudWatch monitoring

AWS CloudWatch basic monitoring (free tier) provides:
- CPU utilization alarm (threshold: >80% sustained for 5 minutes)
- Disk usage alarm via a custom metric pushed by a cron script every 5 minutes (threshold: >80%)
- Memory usage alarm via a custom metric (threshold: >85%)

CloudWatch alarms trigger an SNS notification to the user's email. For v1, these are passive alarms -- no auto-remediation.

### Crash loop detection

The systemd unit is configured with `RestartSec=30` and `StartLimitBurst=5` / `StartLimitIntervalSec=300`. If the container crashes 5 times within 5 minutes:
1. systemd stops restarting
2. The CloudWatch CPU alarm will fire (CPU drops to near zero)
3. Investigation requires SSH access to check logs

## Data Model

All DATETIME columns store UTC values. Conversion to the user's local timezone (from `user_config.timezone`) happens exclusively at the bot/display layer.

### emails
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| gmail_id | TEXT UNIQUE | Dedup key. Inserts use `INSERT OR IGNORE` to handle re-fetch after crash. |
| sender | TEXT | |
| subject | TEXT | |
| summary | TEXT | LLM-generated |
| is_important | BOOLEAN | |
| received_at | DATETIME | UTC |
| processed_at | DATETIME | UTC |

### sender_whitelist
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| pattern | TEXT UNIQUE | "teacher@school.edu" or "*@school.edu" |
| label | TEXT | "School", "Medical" |
| created_at | DATETIME | UTC |

### reminders
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| title | TEXT | |
| description | TEXT | |
| due_at | DATETIME | UTC. Converted to local time at display only. |
| recurrence | TEXT NULL | "daily", "weekly", cron expression |
| status | TEXT | active, snoozed, completed |
| source | TEXT | "email_briefing", "user_manual" |
| source_ref | TEXT NULL | gmail_id if from email |
| created_at | DATETIME | UTC |
| snoozed_until | DATETIME NULL | UTC |

### notes
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| content | TEXT | |
| tags | TEXT | JSON array (acceptable for single-user; see Future Considerations for junction table) |
| created_at | DATETIME | UTC |

### conversations
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| role | TEXT | "user", "assistant", or "context_summary" |
| content | TEXT | |
| timestamp | DATETIME | UTC |

Context window is managed via summarization (see Conversation Context Management below), not hard pruning.

### user_config
| Column | Type | Notes |
|--------|------|-------|
| key | TEXT PK | "timezone", "morning_hour", etc. |
| value | TEXT | |

### health_log
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| event_type | TEXT | "groq_429", "gmail_refresh_error", "circuit_breaker_open", "backup_success", etc. |
| detail | TEXT NULL | Error message or metadata |
| timestamp | DATETIME | UTC |

## Conversation Context Management

Instead of hard-pruning to 20 messages and silently losing context, the system uses LLM-assisted summarization:

1. The conversation window targets the last 20 messages as active context
2. When message 21 arrives, messages 1 through 10 are summarized by the LLM into a single paragraph
3. The summary is stored as a `context_summary` role entry at the beginning of the conversation history
4. The bot sends a brief note to the user: "Older messages in this conversation have been summarized to stay within my context window."
5. If a second summarization is needed (messages accumulate again), the new summary is merged with the previous one
6. The full unsummarized history is retained in the database for potential future reference but is not sent to the LLM

This preserves long-running conversation topics (e.g., planning a trip over multiple days) while keeping LLM token usage bounded.

## Configuration Files

### config/persona.yaml
```yaml
name: openEar
tone: warm, concise, no corporate speak
language:
  primary: English
  supported: [English, Chinese]
  behavior: respond in whatever language the user writes in
email_summary_language: source
behavior:
  - Always acknowledge action items clearly
  - Use emojis sparingly, not every message
  - When unsure, ask rather than guess
  - Keep briefings scannable, not walls of text
```

### config/rules.yaml
```yaml
family:
  sons: []
  husband: {}

email:
  check_times: ["07:00", "20:00"]
  timezone: "America/Los_Angeles"

reminders:
  default_snooze: 1h
  quiet_hours: ["22:00", "07:00"]  # Spans midnight; see quiet hours logic note

notes:
  auto_suggest_reminder: true

health:
  heartbeat_time: "08:00"
  backup_time: "03:00"
```

## Core Flows

### Flow 1: Morning/Evening Email Briefing

1. Scheduler triggers at configured times
2. Gmail API fetches unread since last check (wrapped in OAuth error handling -- see Gmail OAuth Token Lifecycle)
3. Emails are processed in batches:
   - Check each sender against whitelist; if match, mark important and tag with label
   - Remaining emails are batched (up to 10 per call) into a single Groq classification prompt
   - If Groq is unavailable (circuit breaker open), only whitelisted emails are included in the briefing
   - Relevant emails get full summarization via Groq (in source email language), with rate limit retry/backoff
   - All emails stored via `INSERT OR IGNORE` (handles crash-recovery re-fetch)
4. Compose briefing with categorized summaries and extracted action items
5. Send via Telegram with inline keyboards per action item: [Remind Me] [Already Done] [Dismiss]
6. User taps [Remind Me] -> bot asks "When?" -> LLM parses natural language time -> creates reminder (stored as UTC)

### Flow 2: Reminders

1. APScheduler fires at `due_at` time (UTC; scheduler converts from UTC to check against wall clock)
2. Send Telegram message with inline keyboards: [Done] [Snooze 1hr] [Snooze tomorrow] [Repeat weekly]
3. User taps action -> update reminder status accordingly
4. If recurrence set -> auto-schedule next occurrence after completion
5. Quiet hours check: if `start > end` (spans midnight), then `is_quiet = (now >= start or now < end)`. If quiet, defer to next morning.

### Flow 3: Notes

1. User sends note via Telegram (e.g., "note husband has tennis every Thursday 7-9pm")
2. LLM classifies intent as "note", extracts content and tags
3. Store in notes table (created_at in UTC)
4. Bot confirms and offers [Set weekly reminder?] if recurring pattern detected

### Flow 4: Info Lookups (Weather, Stocks, News)

1. User asks a question (e.g., "how's AAPL?" or "weather tomorrow?")
2. LLM classifies intent and selects appropriate tool
3. Tool executes with graceful degradation:
   - **Stocks (yfinance):** Wrapped in try/except. On failure, responds "Stock data is temporarily unavailable -- Yahoo Finance may be experiencing issues. Try again later." Alpha Vantage is a future fallback option.
   - **Weather (Open-Meteo):** Direct API call, reliable. Standard network error handling.
   - **News (DuckDuckGo):** Wrapped in try/except. On failure, responds "News lookup is temporarily unavailable."
4. LLM formats response conversationally

### Flow 5: General Conversation

1. User sends any message not matching above intents
2. Auth guard verifies user ID is in the allowed list (rejects silently if not)
3. LLM responds using conversation context (active messages + summarized older context)
4. Persona rules applied via system prompt

### Flow 6: Health Check (/status)

1. User sends `/status`
2. Bot collects: uptime, DB size, disk/memory usage, last email check, last Groq call, circuit breaker state, last backup timestamp
3. Formats and sends as a single Telegram message

### Flow 7: Daily S3 Backup

1. APScheduler triggers at 03:00 UTC daily
2. SQLite `.backup()` API creates a consistent snapshot to a temporary file
3. Snapshot is uploaded to S3 bucket (`s3://openear-backups/db/openear-YYYY-MM-DD.db`)
4. Retain last 30 daily backups (S3 lifecycle rule deletes older ones)
5. Log success/failure to `health_log` table
6. On failure, send Telegram alert

## Project Structure

```
openEar/
|-- docker-compose.yml
|-- Dockerfile
|-- .env.example                  # Non-sensitive config only (region, log level)
|-- .gitignore
|-- requirements.txt
|-- config/
|   |-- persona.yaml
|   +-- rules.yaml
|-- src/
|   |-- __init__.py
|   |-- main.py
|   |-- config.py                 # Loads secrets from SSM Parameter Store
|   |-- auth.py                   # Telegram user ID filtering
|   |-- bot/
|   |   |-- __init__.py
|   |   |-- handlers.py
|   |   |-- keyboards.py
|   |   +-- formatters.py
|   |-- services/
|   |   |-- __init__.py
|   |   |-- email_service.py      # OAuth error handling, batch classification
|   |   |-- llm_service.py        # Retry, backoff, circuit breaker
|   |   |-- reminder_service.py
|   |   |-- note_service.py
|   |   |-- info_service.py       # yfinance graceful degradation
|   |   |-- backup_service.py     # S3 backup via SQLite .backup()
|   |   +-- health_service.py     # Heartbeat, /status data collection
|   |-- scheduler/
|   |   |-- __init__.py
|   |   +-- jobs.py               # Email checks, heartbeat, backup, CloudWatch metrics
|   +-- db/
|       |-- __init__.py
|       |-- models.py             # All DATETIME fields documented as UTC
|       +-- database.py           # INSERT OR IGNORE for email dedup
|-- scripts/
|   |-- setup_gmail.py            # Local OAuth flow for initial setup
|   |-- reauth_gmail.py           # Temporary Flask endpoint for headless re-auth
|   |-- deploy.sh                 # Tags previous SHA, pulls, builds, verifies logs
|   +-- push_cloudwatch_metrics.sh  # Cron script for disk/memory custom metrics
+-- data/
    +-- openear.db                # Persisted via Docker volume, EBS-encrypted
```

## Deployment

### EC2 Setup
- Instance: t3.micro (1 vCPU, 1GB RAM), Ubuntu 24.04 LTS
- Free tier: 750 hours/month for first 12 months; ~$7.50/month after
- EBS: 20 GB gp3 volume (larger than default 8 GB to accommodate Docker layers, logs, and DB growth), encrypted at rest
- Security group inbound rules:
  - SSH on port 2222 from home/VPN IP CIDR only
  - No other inbound ports (Telegram uses outbound long polling)
- SSH hardening: key-only auth, fail2ban, non-default port
- Docker + Docker Compose installed
- SQLite persisted via Docker named volume at /data/openear.db
- Auto-restart via systemd with crash loop detection (5 restarts in 5 minutes -> stop)
- Cron job every 5 minutes: `push_cloudwatch_metrics.sh` reports disk and memory to CloudWatch

### Deploy Flow
```bash
# SSH to instance
ssh -i key.pem -p 2222 ubuntu@<ec2-ip>

# Tag current state for rollback
cd openear
PREV_SHA=$(git rev-parse HEAD)
echo "Previous SHA: $PREV_SHA"

# Pull and build
git pull && docker compose up -d --build

# Verify startup (wait for healthy logs)
docker compose logs -f --tail=50 &
sleep 10

# If broken, rollback:
# git checkout $PREV_SHA && docker compose up -d --build
```

### Required API Keys / Credentials (stored in SSM Parameter Store)
- `/openear/telegram/bot_token` -- from @BotFather on Telegram
- `/openear/telegram/allowed_user_ids` -- comma-separated numeric IDs
- `/openear/groq/api_key` -- from console.groq.com
- `/openear/gmail/refresh_token` -- generated via scripts/setup_gmail.py
- Gmail OAuth client_id and client_secret -- stored in SSM, not in code or on disk

### S3 Backup Bucket
- Bucket: `openear-backups` (private, versioning disabled, lifecycle rule: delete objects older than 30 days)
- IAM role attached to EC2 instance with policy: `s3:PutObject` and `s3:DeleteObject` on `openear-backups/*`
- No access keys needed -- uses EC2 instance profile

## Future Considerations (not in v1)
- WhatsApp as second messaging channel
- Google Calendar integration for schedule-aware briefings
- Voice messages (Telegram supports them, Groq has Whisper)
- Multi-user support (Postgres migration, user isolation, `note_tags` junction table for efficient tag queries)
- GitHub Actions for auto-deploy on push
- Alpha Vantage or Twelve Data as fallback stock data provider
- NewsAPI as fallback for DuckDuckGo news lookups
- Structured logging (JSON format) for easier log parsing
- Prometheus metrics endpoint for more granular monitoring
