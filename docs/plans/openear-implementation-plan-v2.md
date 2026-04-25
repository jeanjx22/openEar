# Plan: openEar Implementation (v2)

**Goal**: Fully functional personal AI assistant accessible via Telegram
**Architecture**: Single async Python process -- Telegram bot + APScheduler + SQLite + Groq LLM
**Tech Stack**: Python 3.12, python-telegram-bot, SQLAlchemy, APScheduler, Groq, Gmail API, yfinance, duckduckgo-search, httpx, Docker

---

## Changes from previous version

This revision addresses all critical and selected non-critical issues from the [plan review](openear-plan-review-1.md).

### Critical fixes

| ID | Issue | What changed |
|----|-------|--------------|
| C1 | Detached SQLAlchemy objects crash at attribute access | Added `expire_on_commit=False` to `sessionmaker`. All service methods that return ORM objects now call `session.expunge(obj)` (or `session.expunge_all()` for lists) before the session block exits. Applied to every method in ReminderService, NoteService, and the `_match_whitelist`/`_get_conversation_context` paths in EmailService and handlers. |
| C2 | Reminder check fires same reminder every 60 seconds | `_reminder_check_job` now updates each reminder's status to `"notified"` after sending the notification. `get_due_reminders()` only queries `status="active"`. Callback handler transitions from `"notified"` on Done/Snooze. |
| C3 | Missing `__init__.py` files | Added note in Group 1 preamble that `__init__.py` files already exist in the project skeleton and must not be overwritten. Listed in file summary table. |
| C4 | HEALTHCHECK is meaningless | Scheduler now writes `/tmp/openear_heartbeat` timestamp every 60s. HEALTHCHECK verifies the file was modified within the last 2 minutes. |
| C5 | `/start` prerequisite for bot-initiated messages | Added `Forbidden` error handling in `_send_to_all` with clear warning log. Documented the `/start` prerequisite in Step 8A. |
| C6 | Gmail sync calls block the async event loop | All synchronous Gmail API calls in `email_service.py` are wrapped in `await asyncio.to_thread(...)`. |
| C7 | yfinance sync calls block the event loop | `get_stock_quote` in `info_service.py` wraps yfinance calls in `await asyncio.to_thread(...)`. |
| C8 | Secrets exposed via docker inspect | Split `.env.example` into dev vs prod guidance. Production `.env` contains only `LOG_LEVEL`, `AWS_REGION`, `GROQ_MODEL`. Production `docker-compose.yml` does not use `env_file` for secrets. |
| C9 | Missing `reauth_gmail.py` script | Added Step 6D implementing `scripts/reauth_gmail.py` with Flask, automatic 10-minute timeout, and random URL nonce. |
| C10 | No CloudWatch alarm for container liveness | Added note in Step 7A about configuring a CloudWatch alarm on `ContainerRunning == 0` for > 5 minutes. |

### Non-critical fixes

| ID | Issue | What changed |
|----|-------|--------------|
| N1 | Step 3D too large | Split into Step 3D (command handlers), Step 3E (message handler + intent routing), Step 3F (callback handler + conversation context). |
| N4 | `__import__` hack in callback handler | Replaced with normal `from src.db.models import Reminder` import at module top. |
| N5 | Conversation context is global, not per-user | Added `user_id` column to `Conversation` model. Context queries filter by `user_id`. Documented single-user default. |
| N6 | macOS memory calculation wrong | Fixed to `/ (1024 * 1024)` for macOS (bytes), `/ 1024` for Linux (KB). |
| N13 | `boto3` missing from initial `requirements.txt` | Added `boto3` to `requirements.txt` from Step 7A and added a note in Group 1 preamble to install it early. |

---

## Dependency Table

| Group | Name | Depends On | Parallelizable Within Group |
|-------|------|------------|-----------------------------|
| 1 | Foundation (config, database models, database setup) | Nothing | Yes -- all 3 steps are independent |
| 2 | Services (LLM, email, reminder, note, info, health, backup) | Group 1 | Yes -- all 7 services are independent |
| 3 | Bot layer (auth, handlers, keyboards, formatters) | Group 1 + Group 2 | Yes -- auth, keyboards, formatters are independent; handlers (3D-3F) depend on those 3 |
| 4 | Scheduler (jobs, integration) | Group 2 + Group 3 | No -- single step |
| 5 | Main entry point, end-to-end wiring | Group 1-4 | No -- single step |
| 6 | Scripts (Gmail OAuth, deploy, CloudWatch, reauth) | Group 1 | Yes -- all scripts are independent |
| 7 | Docker and deployment verification | Group 1-6 | No -- sequential |
| 8 | End-to-end testing on local machine | Group 1-7 | No -- sequential |

```
Group 1 ──┬── 1A (config.py)
          ├── 1B (models.py)
          └── 1C (database.py)
               │
Group 2 ──┬── 2A (llm_service.py)
          ├── 2B (email_service.py)
          ├── 2C (reminder_service.py)
          ├── 2D (note_service.py)
          ├── 2E (info_service.py)
          ├── 2F (health_service.py)
          └── 2G (backup_service.py)
               │
Group 3 ──┬── 3A (auth.py)
          ├── 3B (keyboards.py)
          ├── 3C (formatters.py)
          ├── 3D (handlers.py -- commands only) ← depends on 3A, 3B, 3C
          ├── 3E (handlers.py -- message handler + intent routing) ← depends on 3D
          └── 3F (handlers.py -- callback handler + context mgmt) ← depends on 3E
               │
Group 4 ──── 4A (scheduler/jobs.py)
               │
Group 5 ──── 5A (main.py)
               │
Group 6 ──┬── 6A (setup_gmail.py)
          ├── 6B (deploy.sh)
          ├── 6C (push_cloudwatch_metrics.sh)
          └── 6D (reauth_gmail.py)
               │
Group 7 ──── 7A (Docker verification)
               │
Group 8 ──── 8A (End-to-end testing)
```

---

## Group 1: Foundation

These three steps have zero dependencies on each other and can be implemented in parallel.

> **Note on `__init__.py` files (C3):** The project skeleton already contains empty `__init__.py` files in `src/`, `src/bot/`, `src/db/`, `src/services/`, and `src/scheduler/`. Do NOT overwrite or recreate them. They are required for Python package imports (`from src.config import ...`, etc.) but are already present.

> **Note on `boto3` (N13):** Steps 1A and 2G use `boto3`. Run `pip install boto3` (or install from `requirements.txt`) before running Group 1 verification commands. The final `requirements.txt` in Step 7A includes `boto3`.

---

### Step 1A: Configuration loader (`src/config.py`)

**File**: `src/config.py`
**Time**: ~5 min
**What**: Loads YAML config files and environment variables. Provides a singleton `Settings` object used everywhere.

```python
"""Configuration loader for openEar.

Loads persona.yaml, rules.yaml, and environment variables into a
single Settings object. In production, secrets come from AWS SSM
Parameter Store via boto3. For local development, secrets come from
a .env file.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Project root is two levels up from src/config.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"


@dataclass
class Settings:
    """Application settings loaded from config files and environment."""

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: set[int] = field(default_factory=set)

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama3-70b-8192"

    # Gmail
    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"

    # Database
    db_path: str = ""

    # Persona
    persona: dict = field(default_factory=dict)

    # Rules
    rules: dict = field(default_factory=dict)

    # Timezone (from rules.yaml)
    timezone: str = "America/Los_Angeles"

    # Logging
    log_level: str = "INFO"
    log_dir: str = ""


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing."""
    if not path.exists():
        logger.warning("Config file not found: %s", path)
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_secrets_from_ssm() -> dict[str, str]:
    """Load secrets from AWS SSM Parameter Store.

    Returns a dict mapping param names to values. Falls back to empty
    dict if boto3 is unavailable or SSM is unreachable (local dev).
    """
    try:
        import boto3

        ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "us-west-2"))
        params = ssm.get_parameters_by_path(
            Path="/openear/",
            Recursive=True,
            WithDecryption=True,
        )
        result = {}
        for p in params.get("Parameters", []):
            # /openear/telegram/bot_token -> telegram_bot_token
            key = p["Name"].replace("/openear/", "").replace("/", "_")
            result[key] = p["Value"]
        return result
    except Exception as e:
        logger.info("SSM unavailable (using .env fallback): %s", e)
        return {}


def load_settings() -> Settings:
    """Load all settings from config files, environment, and optionally SSM."""
    # Load .env for local development
    load_dotenv(PROJECT_ROOT / ".env")

    # Try SSM first, fall back to env vars
    ssm_secrets = _load_secrets_from_ssm()

    # Load YAML configs
    persona = _load_yaml(CONFIG_DIR / "persona.yaml")
    rules = _load_yaml(CONFIG_DIR / "rules.yaml")

    # Parse allowed user IDs
    raw_ids = (
        ssm_secrets.get("telegram_allowed_user_ids", "")
        or os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    )
    allowed_ids: set[int] = set()
    for uid in raw_ids.split(","):
        uid = uid.strip()
        if uid.isdigit():
            allowed_ids.add(int(uid))

    settings = Settings(
        telegram_bot_token=(
            ssm_secrets.get("telegram_bot_token", "")
            or os.getenv("TELEGRAM_BOT_TOKEN", "")
        ),
        telegram_allowed_user_ids=allowed_ids,
        groq_api_key=(
            ssm_secrets.get("groq_api_key", "")
            or os.getenv("GROQ_API_KEY", "")
        ),
        groq_model=os.getenv("GROQ_MODEL", "llama3-70b-8192"),
        gmail_credentials_path=os.getenv(
            "GMAIL_CREDENTIALS_PATH", "credentials.json"
        ),
        gmail_token_path=os.getenv("GMAIL_TOKEN_PATH", "token.json"),
        db_path=str(DATA_DIR / "openear.db"),
        persona=persona,
        rules=rules,
        timezone=rules.get("email", {}).get("timezone", "America/Los_Angeles"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_dir=os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs")),
    )

    return settings
```

**Verify**: `python -c "from src.config import load_settings; s = load_settings(); print(s.timezone, s.persona.get('name'))"` prints `America/Los_Angeles openEar`.

---

### Step 1B: Database models (`src/db/models.py`)

**File**: `src/db/models.py`
**Time**: ~5 min
**What**: SQLAlchemy 2.0 declarative models for all six tables. All DATETIME columns store UTC.

> **N5 fix:** The `Conversation` model now includes a `user_id` column. For single-user deployments, this defaults to `0`. Multi-user deployments should set `user_id` to the Telegram user ID. Context queries filter by `user_id`.

```python
"""SQLAlchemy 2.0 declarative models for openEar.

All DATETIME columns store UTC values. Conversion to user-local
timezone happens exclusively at the bot/display layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gmail_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    sender: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_important: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow
    )


class SenderWhitelist(Base):
    __tablename__ = "sender_whitelist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow
    )


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    recurrence: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, default="active", nullable=False
    )  # "active", "notified", "snoozed", "completed"
    source: Mapped[str] = mapped_column(
        String, default="user_manual", nullable=False
    )
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[str] = mapped_column(
        Text, default="[]", nullable=False
    )  # JSON array stored as text
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )  # Telegram user ID; 0 for single-user default (N5)
    role: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "user", "assistant", "context_summary"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow
    )


class UserConfig(Base):
    __tablename__ = "user_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class HealthLog(Base):
    __tablename__ = "health_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "groq_429", "gmail_refresh_error", "circuit_breaker_open", etc.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow
    )
```

**Verify**: `python -c "from src.db.models import Base, Email, Reminder, Note, Conversation; print('Tables:', [t for t in Base.metadata.tables])"` prints all six table names.

---

### Step 1C: Database setup and session management (`src/db/database.py`)

**File**: `src/db/database.py`
**Time**: ~4 min
**What**: Creates the SQLite engine, provides a session factory with `expire_on_commit=False` (C1), and handles table creation. Uses `INSERT OR IGNORE` pattern via a helper for email dedup.

> **C1 fix:** `sessionmaker` uses `expire_on_commit=False` so that ORM objects remain usable after commit. Service methods that return ORM objects also call `session.expunge()` before the session closes, making them fully safe to use outside the session scope.

```python
"""Database engine, session management, and helpers for openEar.

Uses SQLAlchemy 2.0 with synchronous SQLite (async wrapper via
run_in_executor when called from async code). Provides dedup-safe
insert helper for emails.

C1 fix: sessionmaker uses expire_on_commit=False so returned ORM
objects remain accessible after session close. Service methods
additionally call session.expunge() before returning objects.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def init_db(db_path: str) -> None:
    """Initialize the database engine and create all tables."""
    global _engine, _SessionLocal

    # Ensure the data directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Enable WAL mode for better concurrent read performance
    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(_engine)
    # C1: expire_on_commit=False prevents DetachedInstanceError when
    # accessing ORM object attributes after session.commit()
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    logger.info("Database initialized at %s", db_path)


def get_engine():
    """Return the SQLAlchemy engine (for backup operations)."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


@contextmanager
def get_session() -> Session:
    """Provide a transactional session scope.

    Usage:
        with get_session() as session:
            session.add(some_object)
            # auto-commits on exit, auto-rollbacks on exception

    C1 note: Objects returned from this session remain usable after
    the block exits because expire_on_commit=False. For objects that
    will be used outside the session scope, services should also call
    session.expunge(obj) before the block exits.
    """
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def insert_email_ignore_duplicate(session: Session, email_obj) -> bool:
    """Insert an email, silently ignoring if gmail_id already exists.

    Uses on_conflict_do_nothing targeting the gmail_id unique constraint.
    Returns True if inserted, False if duplicate was ignored.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from src.db.models import Email

    stmt = sqlite_insert(Email).values(
        gmail_id=email_obj.gmail_id,
        sender=email_obj.sender,
        subject=email_obj.subject,
        summary=email_obj.summary,
        is_important=email_obj.is_important,
        received_at=email_obj.received_at,
        processed_at=email_obj.processed_at,
    ).on_conflict_do_nothing(index_elements=["gmail_id"])

    result = session.execute(stmt)
    return result.rowcount > 0
```

**Verify**: Run the following to create a test database, insert a row, and verify dedup works:
```bash
python -c "
from src.db.database import init_db, get_session, insert_email_ignore_duplicate
from src.db.models import Email
from datetime import datetime, timezone

init_db('/tmp/test_openear.db')
now = datetime.now(timezone.utc)

email = Email(gmail_id='test123', sender='a@b.com', subject='Hello', is_important=False, received_at=now, processed_at=now)
with get_session() as s:
    inserted = insert_email_ignore_duplicate(s, email)
    print('First insert:', inserted)  # True

with get_session() as s:
    inserted = insert_email_ignore_duplicate(s, email)
    print('Duplicate insert:', inserted)  # False

print('PASS')
"
```

---

## Group 2: Services

All seven services depend only on Group 1 (config, models, database). They have no dependencies on each other and can be implemented in parallel.

---

### Step 2A: LLM service with retry, backoff, and circuit breaker (`src/services/llm_service.py`)

**File**: `src/services/llm_service.py`
**Time**: ~5 min
**What**: Wraps the Groq SDK with exponential backoff on 429, a circuit breaker that falls back to degraded mode, and rate limit event logging.

```python
"""LLM service wrapping Groq API with retry, backoff, and circuit breaker.

Rate limit handling:
- Exponential backoff: 2s, 4s, 8s, 16s, up to 60s max, 5 attempts
- Circuit breaker: trips after 3 consecutive rate limit failures within
  5 minutes. Resets after 10 minutes with a probe call.
- When circuit is open: classification falls back to whitelist-only,
  summarization falls back to subject + first 200 chars, chat returns
  a "temporarily unavailable" message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from groq import AsyncGroq, RateLimitError

from src.config import Settings
from src.db.database import get_session
from src.db.models import HealthLog

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreaker:
    """Simple circuit breaker for Groq rate limits."""

    failure_count: int = 0
    failure_window_start: float = 0.0
    is_open: bool = False
    opened_at: float = 0.0
    cooldown_seconds: float = 600.0  # 10 minutes
    failure_threshold: int = 3
    window_seconds: float = 300.0  # 5 minutes

    def record_failure(self) -> None:
        now = time.time()
        if now - self.failure_window_start > self.window_seconds:
            self.failure_count = 0
            self.failure_window_start = now
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            self.opened_at = now
            logger.warning("Circuit breaker OPEN after %d failures", self.failure_count)

    def record_success(self) -> None:
        self.failure_count = 0
        if self.is_open:
            self.is_open = False
            logger.info("Circuit breaker CLOSED after successful probe")

    def should_allow(self) -> bool:
        if not self.is_open:
            return True
        elapsed = time.time() - self.opened_at
        if elapsed >= self.cooldown_seconds:
            logger.info("Circuit breaker cooldown expired, allowing probe call")
            return True
        return False


class LLMService:
    """Async LLM service with Groq backend, retry, and circuit breaker."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncGroq(api_key=settings.groq_api_key)
        self.model = settings.groq_model
        self.circuit_breaker = CircuitBreaker()
        self._rate_limit_count_24h: list[float] = []
        self._last_successful_call: float | None = None

    @property
    def rate_limit_count_24h(self) -> int:
        """Number of 429 responses in the last 24 hours."""
        cutoff = time.time() - 86400
        self._rate_limit_count_24h = [
            t for t in self._rate_limit_count_24h if t > cutoff
        ]
        return len(self._rate_limit_count_24h)

    @property
    def last_successful_call(self) -> float | None:
        return self._last_successful_call

    def _log_rate_limit(self, detail: str = "") -> None:
        """Log a rate limit event to the health_log table."""
        self._rate_limit_count_24h.append(time.time())
        try:
            with get_session() as session:
                session.add(HealthLog(event_type="groq_429", detail=detail))
        except Exception as e:
            logger.error("Failed to log rate limit event: %s", e)

    def _log_circuit_breaker(self, state: str) -> None:
        """Log circuit breaker state change."""
        try:
            with get_session() as session:
                session.add(
                    HealthLog(
                        event_type=f"circuit_breaker_{state}",
                        detail=f"failures={self.circuit_breaker.failure_count}",
                    )
                )
        except Exception as e:
            logger.error("Failed to log circuit breaker event: %s", e)

    async def call_groq(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> str | None:
        """Call Groq API with exponential backoff on rate limits.

        Returns the assistant message content, or None if the circuit
        breaker is open and the probe fails.
        """
        if not self.circuit_breaker.should_allow():
            logger.warning("Circuit breaker is open, skipping Groq call")
            return None

        max_retries = 5
        base_delay = 2.0
        max_delay = 60.0
        use_model = model or self.model

        for attempt in range(max_retries):
            try:
                response = await self.client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    temperature=temperature,
                )
                self.circuit_breaker.record_success()
                self._last_successful_call = time.time()
                return response.choices[0].message.content

            except RateLimitError as e:
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(
                    "Groq 429 (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    delay,
                    e,
                )
                self._log_rate_limit(str(e))

                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                else:
                    self.circuit_breaker.record_failure()
                    if self.circuit_breaker.is_open:
                        self._log_circuit_breaker("open")
                    return None

            except Exception as e:
                logger.error("Groq API error: %s", e)
                raise

    async def classify_intent(self, user_message: str) -> dict:
        """Classify user message intent.

        Returns a dict with keys: intent, content, tags.
        intent is one of: "reminder", "note", "weather", "stock",
        "news", "email", "status", "general".
        """
        system_prompt = """You are an intent classifier. Given a user message, classify it into one of these intents:
- "reminder": user wants to set, check, or manage a reminder
- "note": user wants to save a note or piece of information
- "weather": user asks about weather
- "stock": user asks about stocks or market data
- "news": user asks about news
- "general": general conversation

Respond with ONLY a JSON object:
{"intent": "<intent>", "content": "<extracted content>", "tags": ["<tag1>", "<tag2>"]}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        result = await self.call_groq(messages, temperature=0.1)
        if result is None:
            return {"intent": "general", "content": user_message, "tags": []}

        try:
            return json.loads(result)
        except json.JSONDecodeError:
            logger.warning("Failed to parse intent JSON: %s", result)
            return {"intent": "general", "content": user_message, "tags": []}

    async def classify_emails_batch(
        self, emails: list[dict[str, str]], criteria: list[str]
    ) -> list[bool]:
        """Classify a batch of emails as relevant or irrelevant.

        Args:
            emails: list of dicts with "sender" and "subject" keys
            criteria: list of relevance criteria (e.g., ["school", "medical"])

        Returns:
            list of booleans, same order as input. True = relevant.
        """
        if not emails:
            return []

        email_lines = []
        for i, e in enumerate(emails, 1):
            email_lines.append(f'{i}. From: {e["sender"]} | Subject: {e["subject"]}')

        system_prompt = f"""Classify each email as RELEVANT or IRRELEVANT based on these criteria: {', '.join(criteria)}.
Return ONLY a JSON array of booleans in the same order. Example: [true, false, true]

Emails:
{chr(10).join(email_lines)}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Classify these emails."},
        ]
        result = await self.call_groq(messages, temperature=0.1)
        if result is None:
            # Circuit breaker open: return all False (whitelist-only mode)
            return [False] * len(emails)

        try:
            parsed = json.loads(result)
            if isinstance(parsed, list) and len(parsed) == len(emails):
                return [bool(x) for x in parsed]
            logger.warning(
                "Batch classification returned %d items for %d emails",
                len(parsed) if isinstance(parsed, list) else -1,
                len(emails),
            )
            return [False] * len(emails)
        except json.JSONDecodeError:
            logger.warning("Failed to parse batch classification JSON: %s", result)
            return [False] * len(emails)

    async def summarize_email(self, sender: str, subject: str, body: str) -> str:
        """Summarize a single email. Falls back to truncated body if LLM unavailable."""
        system_prompt = """Summarize this email concisely in 2-3 sentences. Preserve the language of the original email. Extract any action items and deadlines."""

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"From: {sender}\nSubject: {subject}\n\n{body[:3000]}",
            },
        ]
        result = await self.call_groq(messages, temperature=0.3)
        if result is None:
            # Fallback: subject + truncated body
            return f"{subject}\n{body[:200]}..."
        return result

    async def summarize_conversation(self, messages_to_summarize: list[dict]) -> str:
        """Summarize older conversation messages into a single paragraph."""
        formatted = "\n".join(
            f'{m["role"]}: {m["content"]}' for m in messages_to_summarize
        )
        system_prompt = """Summarize the following conversation into a single concise paragraph that preserves all key topics, decisions, and action items discussed."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": formatted},
        ]
        result = await self.call_groq(messages, temperature=0.3)
        if result is None:
            # Fallback: just keep the last message content
            return "Previous conversation context unavailable (LLM temporarily down)."
        return result

    async def chat(
        self,
        user_message: str,
        conversation_history: list[dict[str, str]],
        persona: dict,
    ) -> str:
        """General conversation with persona-aware system prompt."""
        persona_name = persona.get("name", "openEar")
        tone = persona.get("tone", "warm, concise")
        behaviors = persona.get("behavior", [])
        behavior_text = "\n".join(f"- {b}" for b in behaviors)

        system_prompt = f"""You are {persona_name}, a personal AI assistant.
Tone: {tone}
Language: Respond in whatever language the user writes in.

Behavioral rules:
{behavior_text}"""

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_message})

        result = await self.call_groq(messages)
        if result is None:
            return "I am temporarily unable to process complex requests. Please try again in a few minutes."
        return result

    async def parse_reminder_time(self, user_input: str) -> dict | None:
        """Parse natural language time into structured reminder data.

        Returns dict with keys: title, due_at (ISO format), recurrence (optional).
        """
        system_prompt = """Parse the user's message into a reminder. Return ONLY a JSON object:
{"title": "<reminder title>", "due_at": "<ISO 8601 datetime>", "recurrence": "<daily|weekly|null>"}

Current UTC time context will be provided. All times in the output must be UTC."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
        result = await self.call_groq(messages, temperature=0.1)
        if result is None:
            return None
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            logger.warning("Failed to parse reminder time: %s", result)
            return None
```

**Verify**: `python -c "from src.services.llm_service import LLMService, CircuitBreaker; cb = CircuitBreaker(); cb.record_failure(); cb.record_failure(); cb.record_failure(); print('Open:', cb.is_open); print('PASS')"` prints `Open: True` then `PASS`.

---

### Step 2B: Email service with OAuth error handling (`src/services/email_service.py`)

**File**: `src/services/email_service.py`
**Time**: ~5 min
**What**: Fetches unread emails from Gmail API, classifies via whitelist + LLM, stores with dedup. Detects OAuth token expiry and raises structured errors.

> **C6 fix:** All synchronous Gmail API calls (`_build_service`, `users().messages().list()`, `.get()`) are wrapped in `await asyncio.to_thread(...)` to avoid blocking the event loop.
> **C1 fix:** The `_match_whitelist` method expunges whitelist entries before the session closes.

```python
"""Gmail email service with OAuth lifecycle management.

Handles:
- Fetching unread emails since last check
- Whitelist matching with glob patterns
- Batch LLM classification for non-whitelisted emails
- OAuth RefreshError detection and alerting
- INSERT OR IGNORE dedup for crash recovery

C6 fix: All synchronous Gmail API calls are wrapped in
asyncio.to_thread() to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.config import Settings
from src.db.database import get_session, insert_email_ignore_duplicate
from src.db.models import Email, SenderWhitelist
from src.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class EmailServiceUnavailable(Exception):
    """Raised when Gmail OAuth token is expired or revoked."""

    pass


class EmailService:
    """Service for fetching and processing Gmail emails."""

    def __init__(self, settings: Settings, llm_service: LLMService) -> None:
        self.settings = settings
        self.llm = llm_service
        self._gmail_service = None
        self._credentials: Credentials | None = None
        self._is_paused = False
        self._last_check: datetime | None = None
        self._pause_reason: str = ""

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def pause_reason(self) -> str:
        return self._pause_reason

    @property
    def last_check(self) -> datetime | None:
        return self._last_check

    def _build_service_sync(self):
        """Build the Gmail API service (synchronous, called via to_thread).

        C6: This method is synchronous because the Google auth and
        discovery libraries use httplib2 which is not async-compatible.
        Always call via asyncio.to_thread().
        """
        if self._is_paused:
            raise EmailServiceUnavailable(self._pause_reason)

        try:
            if self._credentials and self._credentials.valid:
                return self._gmail_service

            if self._credentials and self._credentials.expired:
                self._credentials.refresh(Request())

            if not self._credentials:
                self._credentials = Credentials.from_authorized_user_file(
                    self.settings.gmail_token_path,
                    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                )
                if self._credentials.expired:
                    self._credentials.refresh(Request())

            self._gmail_service = build(
                "gmail", "v1", credentials=self._credentials
            )
            return self._gmail_service

        except RefreshError as e:
            self._is_paused = True
            self._pause_reason = f"OAuth token expired or revoked: {e}"
            logger.error("Gmail OAuth RefreshError: %s", e)
            raise EmailServiceUnavailable(self._pause_reason) from e

        except TransportError as e:
            logger.warning("Gmail transport error (transient): %s", e)
            raise

    def resume(self) -> None:
        """Resume email monitoring after re-authentication."""
        self._is_paused = False
        self._pause_reason = ""
        self._credentials = None
        self._gmail_service = None
        logger.info("Email service resumed")

    async def fetch_unread_emails(
        self, max_results: int = 50
    ) -> list[dict]:
        """Fetch unread emails from Gmail.

        Returns a list of dicts with keys: gmail_id, sender, subject,
        body, received_at.

        C6: All Gmail API calls run in asyncio.to_thread() to avoid
        blocking the event loop.
        """
        # C6: build service in thread (does HTTP for credential refresh)
        service = await asyncio.to_thread(self._build_service_sync)

        query = "is:unread"
        if self._last_check:
            # Gmail query uses epoch seconds
            epoch = int(self._last_check.timestamp())
            query += f" after:{epoch}"

        try:
            # C6: list call in thread
            results = await asyncio.to_thread(
                lambda: service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
        except RefreshError as e:
            self._is_paused = True
            self._pause_reason = f"OAuth token expired during fetch: {e}"
            raise EmailServiceUnavailable(self._pause_reason) from e

        messages = results.get("messages", [])
        emails = []

        for msg_meta in messages:
            try:
                # C6: get call in thread
                msg = await asyncio.to_thread(
                    lambda mid=msg_meta["id"]: service.users()
                    .messages()
                    .get(userId="me", id=mid, format="full")
                    .execute()
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                body = self._extract_body(msg.get("payload", {}))

                received_at = datetime.now(timezone.utc)
                date_str = headers.get("date", "")
                if date_str:
                    try:
                        received_at = parsedate_to_datetime(date_str)
                        if received_at.tzinfo is None:
                            received_at = received_at.replace(tzinfo=timezone.utc)
                        else:
                            received_at = received_at.astimezone(timezone.utc)
                    except Exception:
                        pass

                emails.append(
                    {
                        "gmail_id": msg_meta["id"],
                        "sender": headers.get("from", "unknown"),
                        "subject": headers.get("subject", "(no subject)"),
                        "body": body,
                        "received_at": received_at,
                    }
                )
            except RefreshError as e:
                self._is_paused = True
                self._pause_reason = f"OAuth token expired during message fetch: {e}"
                raise EmailServiceUnavailable(self._pause_reason) from e
            except Exception as e:
                logger.error(
                    "Error fetching message %s: %s", msg_meta.get("id"), e
                )

        self._last_check = datetime.now(timezone.utc)
        return emails

    def _extract_body(self, payload: dict) -> str:
        """Extract plain text body from Gmail message payload."""
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get(
            "data"
        ):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                "utf-8", errors="replace"
            )

        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get(
                "data"
            ):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode(
                    "utf-8", errors="replace"
                )
            if part.get("parts"):
                result = self._extract_body(part)
                if result:
                    return result

        return ""

    def _match_whitelist(self, sender: str) -> str | None:
        """Check sender against whitelist patterns.

        Returns the label if matched, None otherwise.
        Patterns support glob-style matching (e.g., "*@school.edu").
        """
        with get_session() as session:
            whitelist = session.query(SenderWhitelist).all()
            # C1: expunge so objects are usable after session closes
            for entry in whitelist:
                session.expunge(entry)
        for entry in whitelist:
            if fnmatch.fnmatch(sender.lower(), entry.pattern.lower()):
                return entry.label
        return None

    async def process_emails(self) -> list[dict]:
        """Fetch, classify, summarize, and store emails.

        Returns a list of important email dicts ready for briefing.
        """
        raw_emails = await self.fetch_unread_emails()
        if not raw_emails:
            return []

        important_emails = []
        non_whitelisted = []
        non_whitelisted_indices = []

        # Phase 1: whitelist check
        for i, email in enumerate(raw_emails):
            label = self._match_whitelist(email["sender"])
            if label:
                email["label"] = label
                email["is_important"] = True
                important_emails.append(email)
            else:
                non_whitelisted.append(email)
                non_whitelisted_indices.append(i)

        # Phase 2: batch LLM classification for non-whitelisted emails
        if non_whitelisted and not self.llm.circuit_breaker.is_open:
            criteria = ["school", "medical", "urgent", "family"]
            # Process in batches of 10
            for batch_start in range(0, len(non_whitelisted), 10):
                batch = non_whitelisted[batch_start : batch_start + 10]
                batch_data = [
                    {"sender": e["sender"], "subject": e["subject"]} for e in batch
                ]
                results = await self.llm.classify_emails_batch(batch_data, criteria)
                for j, is_relevant in enumerate(results):
                    if is_relevant:
                        batch[j]["is_important"] = True
                        batch[j]["label"] = "LLM-classified"
                        important_emails.append(batch[j])

        # Phase 3: summarize important emails
        for email in important_emails:
            summary = await self.llm.summarize_email(
                email["sender"], email["subject"], email.get("body", "")
            )
            email["summary"] = summary

        # Phase 4: store all emails with dedup
        with get_session() as session:
            for email in raw_emails:
                email_obj = Email(
                    gmail_id=email["gmail_id"],
                    sender=email["sender"],
                    subject=email["subject"],
                    summary=email.get("summary"),
                    is_important=email.get("is_important", False),
                    received_at=email["received_at"],
                )
                insert_email_ignore_duplicate(session, email_obj)

        return important_emails
```

**Verify**: `python -c "from src.services.email_service import EmailService, EmailServiceUnavailable; print('Import OK')"` prints `Import OK`.

---

### Step 2C: Reminder service (`src/services/reminder_service.py`)

**File**: `src/services/reminder_service.py`
**Time**: ~4 min
**What**: CRUD for reminders, snooze logic, recurrence handling, and quiet hours with midnight wraparound.

> **C1 fix:** All methods that return ORM objects call `session.expunge()` (single objects) or iterate and expunge (lists) before the session block exits.
> **C2 fix:** Added `mark_notified()` method. `get_due_reminders()` only queries `status="active"` (unchanged -- was already correct). The scheduler calls `mark_notified()` after sending each reminder notification.

```python
"""Reminder service with quiet hours, snooze, and recurrence.

All times are stored and compared in UTC. The quiet hours check
converts UTC to the user's local timezone for comparison.
Quiet hours spanning midnight are handled correctly:
    if start > end: is_quiet = (now >= start or now < end)

C2 fix: After sending a notification, the scheduler calls
mark_notified() to transition the reminder from "active" to
"notified". get_due_reminders() only returns "active" reminders,
preventing infinite re-notification. User actions (Done/Snooze)
transition from "notified".
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.config import Settings
from src.db.database import get_session
from src.db.models import Reminder

logger = logging.getLogger(__name__)


class ReminderService:
    """Service for managing reminders."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        rules = settings.rules.get("reminders", {})
        self._default_snooze = rules.get("default_snooze", "1h")
        quiet = rules.get("quiet_hours", ["22:00", "07:00"])
        self._quiet_start = time.fromisoformat(quiet[0])
        self._quiet_end = time.fromisoformat(quiet[1])
        self._tz = ZoneInfo(settings.timezone)

    def create_reminder(
        self,
        title: str,
        due_at: datetime,
        description: str | None = None,
        recurrence: str | None = None,
        source: str = "user_manual",
        source_ref: str | None = None,
    ) -> Reminder:
        """Create a new reminder. due_at must be UTC."""
        with get_session() as session:
            reminder = Reminder(
                title=title,
                description=description,
                due_at=due_at,
                recurrence=recurrence,
                source=source,
                source_ref=source_ref,
                status="active",
            )
            session.add(reminder)
            session.flush()
            # C1: expunge before session closes so object remains usable
            session.expunge(reminder)
            logger.info("Created reminder #%d: %s at %s", reminder.id, title, due_at)
        return reminder

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        """Get a reminder by ID."""
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                # C1: expunge before session closes
                session.expunge(reminder)
            return reminder

    def get_active_reminders(self) -> list[Reminder]:
        """Get all active (non-completed, non-snoozed) reminders."""
        with get_session() as session:
            stmt = select(Reminder).where(Reminder.status == "active")
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for r in results:
                session.expunge(r)
            return results

    def get_due_reminders(self) -> list[Reminder]:
        """Get reminders that are due now (due_at <= now, status=active).

        C2: Only returns reminders with status="active". Once a reminder
        is notified via mark_notified(), it will not appear here again.
        """
        now = datetime.now(timezone.utc)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.status == "active")
                .where(Reminder.due_at <= now)
            )
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for r in results:
                session.expunge(r)
            return results

    def get_snoozed_due(self) -> list[Reminder]:
        """Get snoozed reminders whose snooze has expired."""
        now = datetime.now(timezone.utc)
        with get_session() as session:
            stmt = (
                select(Reminder)
                .where(Reminder.status == "snoozed")
                .where(Reminder.snoozed_until <= now)
            )
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for r in results:
                session.expunge(r)
            return results

    def mark_notified(self, reminder_id: int) -> None:
        """Mark a reminder as notified after sending notification.

        C2: Transitions status from "active" to "notified" so it will
        not be picked up by get_due_reminders() again. The user must
        take action (Done/Snooze) to transition from "notified".
        """
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder and reminder.status == "active":
                reminder.status = "notified"
                logger.info("Reminder #%d marked as notified", reminder_id)

    def complete_reminder(self, reminder_id: int) -> Reminder | None:
        """Mark a reminder as completed. If recurring, schedule next occurrence.

        C2: Accepts reminders in "active" or "notified" status.
        """
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if not reminder:
                return None
            reminder.status = "completed"

            # Handle recurrence
            if reminder.recurrence:
                next_due = self._calculate_next_due(
                    reminder.due_at, reminder.recurrence
                )
                new_reminder = Reminder(
                    title=reminder.title,
                    description=reminder.description,
                    due_at=next_due,
                    recurrence=reminder.recurrence,
                    source=reminder.source,
                    source_ref=reminder.source_ref,
                    status="active",
                )
                session.add(new_reminder)
                logger.info(
                    "Created recurring reminder: %s at %s",
                    new_reminder.title,
                    next_due,
                )

            # C1: expunge before session closes
            session.expunge(reminder)
            return reminder

    def snooze_reminder(
        self, reminder_id: int, duration: str = ""
    ) -> Reminder | None:
        """Snooze a reminder for the given duration.

        Duration format: "1h", "30m", "1d", "tomorrow".
        Empty string uses the configured default.
        C2: Accepts reminders in "active" or "notified" status.
        """
        if not duration:
            duration = self._default_snooze

        delta = self._parse_duration(duration)
        with get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if not reminder:
                return None
            reminder.status = "snoozed"
            reminder.snoozed_until = datetime.now(timezone.utc) + delta
            # C1: expunge before session closes
            session.expunge(reminder)
            return reminder

    def is_quiet_hours(self) -> bool:
        """Check if current local time is within quiet hours.

        Handles midnight wraparound:
        If quiet_start > quiet_end (e.g., 22:00 to 07:00),
        then is_quiet = (now >= start OR now < end).
        """
        now_local = datetime.now(self._tz).time()

        if self._quiet_start > self._quiet_end:
            # Spans midnight
            return now_local >= self._quiet_start or now_local < self._quiet_end
        else:
            return self._quiet_start <= now_local < self._quiet_end

    def next_morning_utc(self) -> datetime:
        """Return the UTC datetime for the next quiet_end in local time."""
        now_local = datetime.now(self._tz)
        next_morning = now_local.replace(
            hour=self._quiet_end.hour,
            minute=self._quiet_end.minute,
            second=0,
            microsecond=0,
        )
        if next_morning <= now_local:
            next_morning += timedelta(days=1)
        return next_morning.astimezone(timezone.utc)

    def _calculate_next_due(self, current_due: datetime, recurrence: str) -> datetime:
        """Calculate the next due date based on recurrence pattern."""
        if recurrence == "daily":
            return current_due + timedelta(days=1)
        elif recurrence == "weekly":
            return current_due + timedelta(weeks=1)
        elif recurrence == "monthly":
            # Approximate: add 30 days
            return current_due + timedelta(days=30)
        else:
            # Default to daily for unknown patterns
            return current_due + timedelta(days=1)

    def _parse_duration(self, duration: str) -> timedelta:
        """Parse a duration string like '1h', '30m', '1d', 'tomorrow'."""
        duration = duration.strip().lower()
        if duration == "tomorrow":
            return timedelta(days=1)
        if duration.endswith("h"):
            return timedelta(hours=int(duration[:-1]))
        if duration.endswith("m"):
            return timedelta(minutes=int(duration[:-1]))
        if duration.endswith("d"):
            return timedelta(days=int(duration[:-1]))
        # Default: 1 hour
        return timedelta(hours=1)
```

**Verify**: `python -c "from src.services.reminder_service import ReminderService; print('Import OK')"` prints `Import OK`.

---

### Step 2D: Note service (`src/services/note_service.py`)

**File**: `src/services/note_service.py`
**Time**: ~3 min
**What**: CRUD for notes with JSON tag storage and search.

> **C1 fix:** All methods that return ORM objects call `session.expunge()` before the session block exits.

```python
"""Note service for saving and retrieving notes.

Tags are stored as a JSON array in a TEXT column. This is acceptable
for single-user use. A note_tags junction table would be needed for
multi-user (see Future Considerations in design doc).

C1 fix: All methods call session.expunge() on returned ORM objects
before the session block exits.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from src.db.database import get_session
from src.db.models import Note

logger = logging.getLogger(__name__)


class NoteService:
    """Service for managing notes."""

    def save_note(self, content: str, tags: list[str] | None = None) -> Note:
        """Save a new note with optional tags."""
        tags_json = json.dumps(tags or [])
        with get_session() as session:
            note = Note(content=content, tags=tags_json)
            session.add(note)
            session.flush()
            # C1: expunge before session closes
            session.expunge(note)
            logger.info("Saved note #%d with tags %s", note.id, tags or [])
        return note

    def get_note(self, note_id: int) -> Note | None:
        """Get a note by ID."""
        with get_session() as session:
            note = session.get(Note, note_id)
            if note:
                # C1: expunge before session closes
                session.expunge(note)
            return note

    def get_all_notes(self, limit: int = 50) -> list[Note]:
        """Get all notes, most recent first."""
        with get_session() as session:
            stmt = select(Note).order_by(Note.created_at.desc()).limit(limit)
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for note in results:
                session.expunge(note)
            return results

    def search_notes(self, query: str) -> list[Note]:
        """Search notes by content (case-insensitive LIKE)."""
        with get_session() as session:
            stmt = select(Note).where(
                Note.content.ilike(f"%{query}%")
            )
            results = list(session.execute(stmt).scalars().all())
            # C1: expunge all before session closes
            for note in results:
                session.expunge(note)
            return results

    def search_by_tag(self, tag: str) -> list[Note]:
        """Search notes by tag. Scans JSON array in tags column."""
        with get_session() as session:
            # SQLite JSON: tags column contains JSON array as text
            # Use LIKE as a simple filter, then verify in Python
            stmt = select(Note).where(Note.tags.ilike(f'%"{tag}"%'))
            candidates = list(session.execute(stmt).scalars().all())
            results = []
            for note in candidates:
                # C1: expunge before session closes
                session.expunge(note)
                try:
                    note_tags = json.loads(note.tags)
                    if tag.lower() in [t.lower() for t in note_tags]:
                        results.append(note)
                except json.JSONDecodeError:
                    pass
            return results

    def delete_note(self, note_id: int) -> bool:
        """Delete a note by ID. Returns True if deleted."""
        with get_session() as session:
            note = session.get(Note, note_id)
            if not note:
                return False
            session.delete(note)
            return True
```

**Verify**: `python -c "from src.services.note_service import NoteService; print('Import OK')"` prints `Import OK`.

---

### Step 2E: Info service -- weather, stocks, news (`src/services/info_service.py`)

**File**: `src/services/info_service.py`
**Time**: ~5 min
**What**: Wrappers for Open-Meteo (weather), yfinance (stocks), and DuckDuckGo (news) with graceful degradation on failure.

> **C7 fix:** `get_stock_quote` wraps synchronous yfinance calls in `await asyncio.to_thread(...)` to avoid blocking the event loop. The synchronous work is extracted into `_get_stock_quote_sync`.

```python
"""Info services for weather, stocks, and news lookups.

Each service is wrapped in try/except with graceful degradation:
- Weather (Open-Meteo): free, no API key, reliable
- Stocks (yfinance): unofficial Yahoo Finance scraper, may break
- News (DuckDuckGo): unofficial scraper, may be rate-limited

All failures return user-friendly error messages rather than raising.

C7 fix: yfinance calls are wrapped in asyncio.to_thread() because
yfinance uses synchronous HTTP (urllib3/requests) internally.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


async def get_weather(
    latitude: float = 37.39,
    longitude: float = -122.08,
    city_name: str = "San Jose",
) -> str:
    """Get current weather and forecast from Open-Meteo.

    Returns a formatted string or an error message.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "timezone": "America/Los_Angeles",
                    "forecast_days": 3,
                },
            )
            response.raise_for_status()
            data = response.json()

            current = data.get("current", {})
            daily = data.get("daily", {})

            temp = current.get("temperature_2m", "N/A")
            humidity = current.get("relative_humidity_2m", "N/A")
            wind = current.get("wind_speed_10m", "N/A")

            lines = [
                f"Weather in {city_name}:",
                f"Now: {temp}F, Humidity: {humidity}%, Wind: {wind} mph",
                "",
                "Forecast:",
            ]

            dates = daily.get("time", [])
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            rain = daily.get("precipitation_probability_max", [])

            for i in range(min(3, len(dates))):
                lines.append(
                    f"  {dates[i]}: {lows[i]}F - {highs[i]}F, "
                    f"Rain: {rain[i]}%"
                )

            return "\n".join(lines)

    except Exception as e:
        logger.error("Weather lookup failed: %s", e)
        return "Weather data is temporarily unavailable. Please try again later."


def _get_stock_quote_sync(symbol: str) -> str:
    """Synchronous stock quote fetch (runs in asyncio.to_thread).

    C7: yfinance uses urllib3/requests internally and blocks. This
    function is called via asyncio.to_thread() from the async wrapper.
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol.upper())
    info = ticker.info

    if not info or "regularMarketPrice" not in info:
        # Try fast_info as fallback
        fast = ticker.fast_info
        price = getattr(fast, "last_price", None)
        prev_close = getattr(fast, "previous_close", None)
        if price is None:
            return f"No data available for {symbol.upper()}."
        change = (
            f" ({((price - prev_close) / prev_close * 100):+.2f}%)"
            if prev_close
            else ""
        )
        return f"{symbol.upper()}: ${price:.2f}{change}"

    price = info.get("regularMarketPrice", "N/A")
    prev_close = info.get("regularMarketPreviousClose", 0)
    name = info.get("shortName", symbol.upper())
    market_cap = info.get("marketCap", 0)

    change_pct = ""
    if prev_close and price != "N/A":
        change_pct = f" ({((price - prev_close) / prev_close * 100):+.2f}%)"

    cap_str = ""
    if market_cap:
        if market_cap >= 1e12:
            cap_str = f"${market_cap / 1e12:.1f}T"
        elif market_cap >= 1e9:
            cap_str = f"${market_cap / 1e9:.1f}B"
        else:
            cap_str = f"${market_cap / 1e6:.0f}M"

    return (
        f"{name} ({symbol.upper()})\n"
        f"Price: ${price:.2f}{change_pct}\n"
        f"Market Cap: {cap_str}"
    )


async def get_stock_quote(symbol: str) -> str:
    """Get stock quote from yfinance.

    Wrapped with graceful degradation. Returns a formatted string
    or an error message if Yahoo Finance is unavailable.

    C7: The actual yfinance calls run in asyncio.to_thread() to avoid
    blocking the event loop.
    """
    try:
        return await asyncio.to_thread(_get_stock_quote_sync, symbol)
    except Exception as e:
        logger.error("Stock lookup failed for %s: %s", symbol, e)
        return (
            f"Stock data is temporarily unavailable for {symbol.upper()} -- "
            "Yahoo Finance may be experiencing issues. Try again later."
        )


async def search_news(query: str, max_results: int = 5) -> str:
    """Search news via DuckDuckGo.

    Returns formatted results or an error message.
    """
    try:
        from duckduckgo_search import AsyncDDGS

        async with AsyncDDGS() as ddgs:
            results = []
            async for r in ddgs.anews(query, max_results=max_results):
                results.append(r)

            if not results:
                return f"No news found for '{query}'."

            lines = [f"News for '{query}':"]
            for i, r in enumerate(results, 1):
                title = r.get("title", "Untitled")
                source = r.get("source", "")
                date = r.get("date", "")
                body = r.get("body", "")[:100]
                lines.append(f"\n{i}. {title}")
                if source:
                    lines.append(f"   Source: {source}")
                if date:
                    lines.append(f"   Date: {date}")
                if body:
                    lines.append(f"   {body}...")

            return "\n".join(lines)

    except Exception as e:
        logger.error("News lookup failed: %s", e)
        return "News lookup is temporarily unavailable. Please try again later."
```

**Verify**: `python -c "from src.services.info_service import get_weather, get_stock_quote, search_news; print('Import OK')"` prints `Import OK`.

---

### Step 2F: Health service (`src/services/health_service.py`)

**File**: `src/services/health_service.py`
**Time**: ~4 min
**What**: Collects system health data for the heartbeat and `/status` command. Tracks uptime, DB size, disk/memory, last check timestamps.

> **N6 fix:** macOS memory calculation uses `/ (1024 * 1024)` (bytes to MB), Linux uses `/ 1024` (KB to MB).

```python
"""Health service for heartbeat and /status data collection.

Provides system health metrics:
- Uptime (from process start time)
- Database size
- Disk and memory usage
- Last email check and Groq call timestamps
- Circuit breaker state
- Rate limit counts
- Last backup timestamp
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from src.config import Settings
from src.db.database import get_session
from src.db.models import Email, HealthLog, Reminder

logger = logging.getLogger(__name__)

_start_time = time.time()


class HealthService:
    """Collects and formats health metrics."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_uptime(self) -> str:
        """Get formatted uptime string."""
        elapsed = time.time() - _start_time
        days = int(elapsed // 86400)
        hours = int((elapsed % 86400) // 3600)
        minutes = int((elapsed % 3600) // 60)
        return f"{days}d {hours}h {minutes}m"

    def get_db_size(self) -> str:
        """Get database file size."""
        db_path = Path(self.settings.db_path)
        if db_path.exists():
            size_bytes = db_path.stat().st_size
            if size_bytes >= 1024 * 1024:
                return f"{size_bytes / (1024 * 1024):.1f} MB"
            return f"{size_bytes / 1024:.1f} KB"
        return "N/A"

    def get_disk_usage(self) -> str:
        """Get disk usage of the data partition."""
        try:
            usage = shutil.disk_usage(self.settings.db_path or "/")
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            return f"{free_gb:.1f} GB / {total_gb:.1f} GB"
        except Exception:
            return "N/A"

    def get_memory_usage(self) -> str:
        """Get process memory usage (RSS).

        N6 fix: macOS ru_maxrss is in bytes, Linux is in KB.
        """
        try:
            import resource

            rusage = resource.getrusage(resource.RUSAGE_SELF)
            if os.uname().sysname == "Darwin":
                # macOS reports ru_maxrss in bytes
                mem_mb = rusage.ru_maxrss / (1024 * 1024)
            else:
                # Linux reports ru_maxrss in KB
                mem_mb = rusage.ru_maxrss / 1024
            return f"{mem_mb:.0f} MB"
        except Exception:
            return "N/A"

    def get_emails_processed_today(self) -> int:
        """Count emails processed today (UTC)."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        try:
            with get_session() as session:
                count = (
                    session.execute(
                        select(func.count(Email.id)).where(
                            Email.processed_at >= today_start
                        )
                    )
                    .scalar()
                )
                return count or 0
        except Exception:
            return 0

    def get_pending_reminders(self) -> int:
        """Count active reminders."""
        try:
            with get_session() as session:
                count = (
                    session.execute(
                        select(func.count(Reminder.id)).where(
                            Reminder.status == "active"
                        )
                    )
                    .scalar()
                )
                return count or 0
        except Exception:
            return 0

    def get_last_backup_info(self) -> str:
        """Get last successful backup timestamp."""
        try:
            with get_session() as session:
                result = (
                    session.execute(
                        select(HealthLog)
                        .where(HealthLog.event_type == "backup_success")
                        .order_by(HealthLog.timestamp.desc())
                        .limit(1)
                    )
                    .scalar_one_or_none()
                )
                if result:
                    ts = result.timestamp.strftime("%Y-%m-%d %H:%M UTC")
                    session.expunge(result)
                    return ts
                return "Never"
        except Exception:
            return "Unknown"

    def get_last_error(self) -> str:
        """Get the most recent error from health_log."""
        try:
            with get_session() as session:
                error_types = [
                    "groq_429",
                    "gmail_refresh_error",
                    "backup_failure",
                    "circuit_breaker_open",
                ]
                result = (
                    session.execute(
                        select(HealthLog)
                        .where(HealthLog.event_type.in_(error_types))
                        .order_by(HealthLog.timestamp.desc())
                        .limit(1)
                    )
                    .scalar_one_or_none()
                )
                if result:
                    age_hours = (
                        datetime.now(timezone.utc) - result.timestamp
                    ).total_seconds() / 3600
                    info = (
                        f"{result.event_type}: {result.detail or 'no detail'} "
                        f"({age_hours:.0f}h ago)"
                    )
                    session.expunge(result)
                    return info
                return "none"
        except Exception:
            return "Unknown"

    def format_heartbeat(
        self,
        groq_status: str = "OK",
        groq_429_count: int = 0,
        gmail_status: str = "Connected",
        email_check_times: list[str] | None = None,
    ) -> str:
        """Format a daily heartbeat message."""
        check_times = email_check_times or ["07:00", "20:00"]
        return (
            "openEar health report:\n"
            f"- Uptime: {self.get_uptime()}\n"
            f"- Emails processed today: {self.get_emails_processed_today()} "
            f"(next check: {check_times[0]})\n"
            f"- Pending reminders: {self.get_pending_reminders()}\n"
            f"- DB size: {self.get_db_size()}\n"
            f"- Disk free: {self.get_disk_usage()}\n"
            f"- Memory: {self.get_memory_usage()}\n"
            f"- Groq status: {groq_status} ({groq_429_count} rate limits in 24h)\n"
            f"- Gmail status: {gmail_status}\n"
            f"- Last backup: {self.get_last_backup_info()}\n"
            f"- Last error: {self.get_last_error()}"
        )

    def format_status(
        self,
        groq_status: str = "OK",
        groq_429_count: int = 0,
        gmail_status: str = "Connected",
        last_email_check: str = "N/A",
        last_groq_call: str = "N/A",
        circuit_breaker_state: str = "CLOSED",
    ) -> str:
        """Format a /status response (real-time version of heartbeat)."""
        return (
            "openEar status:\n"
            f"- Uptime: {self.get_uptime()}\n"
            f"- Emails processed today: {self.get_emails_processed_today()}\n"
            f"- Pending reminders: {self.get_pending_reminders()}\n"
            f"- DB size: {self.get_db_size()}\n"
            f"- Disk free: {self.get_disk_usage()}\n"
            f"- Memory: {self.get_memory_usage()}\n"
            f"- Groq: {groq_status} ({groq_429_count} rate limits in 24h)\n"
            f"- Circuit breaker: {circuit_breaker_state}\n"
            f"- Gmail: {gmail_status}\n"
            f"- Last email check: {last_email_check}\n"
            f"- Last Groq call: {last_groq_call}\n"
            f"- Last backup: {self.get_last_backup_info()}\n"
            f"- Last error: {self.get_last_error()}"
        )
```

**Verify**: `python -c "from src.services.health_service import HealthService; print('Import OK')"` prints `Import OK`.

---

### Step 2G: Backup service (`src/services/backup_service.py`)

**File**: `src/services/backup_service.py`
**Time**: ~3 min
**What**: SQLite `.backup()` to a temp file, then upload to S3. Logs success/failure to health_log.

```python
"""S3 backup service using SQLite's .backup() API.

Runs daily via APScheduler. Creates a consistent snapshot even while
the application is writing. Uses EC2 instance profile for S3 access
(no access keys). Logs success/failure to health_log table.
"""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.config import Settings
from src.db.database import get_session
from src.db.models import HealthLog

logger = logging.getLogger(__name__)


class BackupService:
    """Handles SQLite database backups to S3."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket_name = "openear-backups"
        self.db_path = settings.db_path

    async def run_backup(self) -> bool:
        """Create a SQLite backup and upload to S3.

        Returns True on success, False on failure.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        s3_key = f"db/openear-{today}.db"

        try:
            # Step 1: Create consistent snapshot via SQLite backup API
            with tempfile.NamedTemporaryFile(
                suffix=".db", delete=False
            ) as tmp:
                tmp_path = tmp.name

            source_conn = sqlite3.connect(self.db_path)
            dest_conn = sqlite3.connect(tmp_path)
            source_conn.backup(dest_conn)
            source_conn.close()
            dest_conn.close()

            backup_size = Path(tmp_path).stat().st_size
            logger.info(
                "Backup snapshot created: %s (%.1f MB)",
                tmp_path,
                backup_size / (1024 * 1024),
            )

            # Step 2: Upload to S3
            try:
                import boto3

                s3 = boto3.client("s3")
                s3.upload_file(tmp_path, self.bucket_name, s3_key)
                logger.info("Backup uploaded to s3://%s/%s", self.bucket_name, s3_key)
            except Exception as e:
                logger.error("S3 upload failed: %s", e)
                self._log_event("backup_failure", f"S3 upload failed: {e}")
                return False
            finally:
                # Clean up temp file
                Path(tmp_path).unlink(missing_ok=True)

            # Step 3: Log success
            self._log_event(
                "backup_success",
                f"s3://{self.bucket_name}/{s3_key} ({backup_size} bytes)",
            )
            return True

        except Exception as e:
            logger.error("Backup failed: %s", e)
            self._log_event("backup_failure", str(e))
            return False

    def _log_event(self, event_type: str, detail: str) -> None:
        """Log a backup event to the health_log table."""
        try:
            with get_session() as session:
                session.add(HealthLog(event_type=event_type, detail=detail))
        except Exception as e:
            logger.error("Failed to log backup event: %s", e)
```

**Verify**: `python -c "from src.services.backup_service import BackupService; print('Import OK')"` prints `Import OK`.

---

## Group 3: Bot Layer

Steps 3A, 3B, and 3C have no dependencies on each other (only on Group 1 + 2) and can be implemented in parallel. Steps 3D-3F are sequential and depend on 3A, 3B, 3C.

> **N1 fix:** The original Step 3D (~250 lines, 8 command handlers + message router + callback handler + context management) has been split into three steps: 3D (command handlers), 3E (message handler + intent routing), 3F (callback handler + conversation context management). All three are parts of the same file `src/bot/handlers.py`.

---

### Step 3A: Auth guard (`src/auth.py`)

**File**: `src/auth.py`
**Time**: ~2 min
**What**: Telegram user ID filter applied as the outermost check in every handler. Unauthorized messages are silently dropped.

```python
"""Telegram user ID authentication filter.

Applied as the outermost filter in every handler group. Messages
from unauthorized users are silently dropped -- no response, no LLM
call, no logging of message content (only the rejected user ID).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def auth_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the user is authorized.

    Returns True if allowed, False if rejected.
    Unauthorized messages are silently dropped.
    """
    allowed_ids: set[int] = context.bot_data.get("allowed_user_ids", set())

    if not update.effective_user:
        return False

    user_id = update.effective_user.id
    if user_id not in allowed_ids:
        logger.warning("Rejected message from user_id=%s", user_id)
        return False

    return True
```

**Verify**: `python -c "from src.auth import auth_check; print('Import OK')"` prints `Import OK`.

---

### Step 3B: Inline keyboards (`src/bot/keyboards.py`)

**File**: `src/bot/keyboards.py`
**Time**: ~3 min
**What**: Factory functions for inline keyboard markup used across handlers -- briefing actions, reminder actions, note suggestions.

```python
"""Inline keyboard builders for Telegram bot interactions.

Provides keyboard markup for:
- Email briefing action items (Remind Me, Already Done, Dismiss)
- Reminder actions (Done, Snooze 1hr, Snooze tomorrow, Repeat weekly)
- Note follow-ups (Set reminder?, Search related)
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def briefing_actions(email_index: int) -> InlineKeyboardMarkup:
    """Inline keyboard for email briefing action items."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Remind Me",
                    callback_data=f"email_remind:{email_index}",
                ),
                InlineKeyboardButton(
                    "Already Done",
                    callback_data=f"email_done:{email_index}",
                ),
                InlineKeyboardButton(
                    "Dismiss",
                    callback_data=f"email_dismiss:{email_index}",
                ),
            ]
        ]
    )


def reminder_actions(reminder_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for reminder notifications."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Done", callback_data=f"reminder_done:{reminder_id}"
                ),
                InlineKeyboardButton(
                    "Snooze 1hr",
                    callback_data=f"reminder_snooze_1h:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Snooze tomorrow",
                    callback_data=f"reminder_snooze_tomorrow:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "Repeat weekly",
                    callback_data=f"reminder_repeat_weekly:{reminder_id}",
                ),
            ],
        ]
    )


def note_followup(note_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for note follow-up suggestions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Set reminder?",
                    callback_data=f"note_remind:{note_id}",
                ),
            ]
        ]
    )


def confirm_cancel() -> InlineKeyboardMarkup:
    """Generic confirm/cancel keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data="confirm"),
                InlineKeyboardButton("Cancel", callback_data="cancel"),
            ]
        ]
    )
```

**Verify**: `python -c "from src.bot.keyboards import briefing_actions, reminder_actions; kb = reminder_actions(42); print(kb.inline_keyboard[0][0].text)"` prints `Done`.

---

### Step 3C: Message formatters (`src/bot/formatters.py`)

**File**: `src/bot/formatters.py`
**Time**: ~3 min
**What**: Functions that format briefings, reminders, notes, and status messages for Telegram display. Converts UTC to local time for display.

```python
"""Message formatters for Telegram display.

All datetime values stored in the database are UTC. These formatters
convert to the user's local timezone for display purposes only.
"""

from __future__ import annotations

from datetime import datetime, timezone

from zoneinfo import ZoneInfo


def to_local(dt: datetime, tz_name: str = "America/Los_Angeles") -> str:
    """Convert UTC datetime to local time string for display."""
    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)
    return local.strftime("%b %d, %I:%M %p")


def format_briefing(emails: list[dict], tz_name: str = "America/Los_Angeles") -> str:
    """Format an email briefing message."""
    if not emails:
        return "No important emails right now."

    lines = [f"You have {len(emails)} important email(s):\n"]
    for i, email in enumerate(emails, 1):
        label = email.get("label", "")
        label_tag = f" [{label}]" if label else ""
        received = email.get("received_at")
        time_str = ""
        if received:
            time_str = f" ({to_local(received, tz_name)})"

        lines.append(f"{i}. {email['subject']}{label_tag}{time_str}")
        lines.append(f"   From: {email['sender']}")
        if email.get("summary"):
            lines.append(f"   {email['summary']}")
        lines.append("")

    return "\n".join(lines)


def format_reminder(reminder, tz_name: str = "America/Los_Angeles") -> str:
    """Format a single reminder for display."""
    due_str = to_local(reminder.due_at, tz_name)
    recurrence = f" (repeats {reminder.recurrence})" if reminder.recurrence else ""
    desc = f"\n{reminder.description}" if reminder.description else ""
    return f"Reminder: {reminder.title}\nDue: {due_str}{recurrence}{desc}"


def format_reminder_list(
    reminders: list, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format a list of reminders."""
    if not reminders:
        return "No active reminders."

    lines = [f"Active reminders ({len(reminders)}):\n"]
    for r in reminders:
        due_str = to_local(r.due_at, tz_name)
        status_emoji = {"active": "", "snoozed": " (snoozed)", "completed": " (done)"}
        lines.append(
            f"  #{r.id}: {r.title} - {due_str}{status_emoji.get(r.status, '')}"
        )
    return "\n".join(lines)


def format_note(note, tz_name: str = "America/Los_Angeles") -> str:
    """Format a single note for display."""
    created = to_local(note.created_at, tz_name)
    tags = ""
    if note.tags and note.tags != "[]":
        import json

        try:
            tag_list = json.loads(note.tags)
            if tag_list:
                tags = f"\nTags: {', '.join(tag_list)}"
        except Exception:
            pass
    return f"Note #{note.id} ({created}):\n{note.content}{tags}"


def format_note_list(
    notes: list, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format a list of notes."""
    if not notes:
        return "No notes saved."

    lines = [f"Notes ({len(notes)}):\n"]
    for n in notes:
        created = to_local(n.created_at, tz_name)
        preview = n.content[:60] + ("..." if len(n.content) > 60 else "")
        lines.append(f"  #{n.id} ({created}): {preview}")
    return "\n".join(lines)
```

**Verify**: `python -c "from src.bot.formatters import format_briefing; print(format_briefing([]))"` prints `No important emails right now.`.

---

### Step 3D: Bot handlers -- command handlers (`src/bot/handlers.py`, part 1 of 3)

**File**: `src/bot/handlers.py`
**Time**: ~5 min
**What**: Class skeleton and command handlers (/start, /help, /status, /briefing, /reminders, /notes, /note). This is the first of three steps for this file.

> **N1 fix:** The original monolithic Step 3D is split into 3D (commands), 3E (message handler), and 3F (callbacks + context).
> **N4 fix:** Uses normal `from src.db.models import Reminder` import instead of `__import__` hack.
> **N5 fix:** Conversation context queries filter by `user_id`.

**Depends on**: 3A (auth), 3B (keyboards), 3C (formatters), all Group 2 services

```python
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
import time
from datetime import datetime, timezone

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
from src.db.models import Conversation, Reminder  # N4: normal import
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
        self._pending_reminder_context: dict[int, dict] = {}

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
            CallbackQueryHandler(self.callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message),
        ]

    # ---- Command Handlers (Step 3D) ----

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
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
        reminders = self.reminders.get_active_reminders()
        text = formatters.format_reminder_list(reminders, self.settings.timezone)
        await update.message.reply_text(text)

    async def cmd_list_notes(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
        notes = self.notes.get_all_notes()
        text = formatters.format_note_list(notes, self.settings.timezone)
        await update.message.reply_text(text)

    async def cmd_save_note(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return
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
```

**Verify**: `python -c "from src.bot.handlers import BotHandlers; print('Import OK')"` -- will fail until Steps 3E and 3F are appended. Verify after 3F.

---

### Step 3E: Bot handlers -- message handler + intent routing (`src/bot/handlers.py`, part 2 of 3)

**File**: `src/bot/handlers.py` (append to Step 3D)
**Time**: ~4 min
**What**: The `handle_message` method that classifies user intent and routes to the appropriate service (weather, stock, news, note, reminder, general chat).

Append the following methods to the `BotHandlers` class in `src/bot/handlers.py`:

```python
    # ---- Message Handler (general conversation + intent routing) (Step 3E) ----

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return

        user_message = update.message.text
        user_id = update.effective_user.id

        # Classify intent
        intent_data = await self.llm.classify_intent(user_message)
        intent = intent_data.get("intent", "general")

        if intent == "weather":
            result = await get_weather()
            await update.message.reply_text(result)
        elif intent == "stock":
            # Extract ticker symbol from content
            content = intent_data.get("content", user_message)
            # Simple extraction: take the first word that looks like a ticker
            words = content.upper().split()
            symbol = next(
                (w for w in words if w.isalpha() and len(w) <= 5),
                content.split()[-1] if content.split() else "SPY",
            )
            result = await get_stock_quote(symbol)
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
            parsed = await self.llm.parse_reminder_time(user_message)
            if parsed:
                try:
                    due_at = datetime.fromisoformat(parsed["due_at"])
                    if due_at.tzinfo is None:
                        due_at = due_at.replace(tzinfo=timezone.utc)
                    reminder = self.reminders.create_reminder(
                        title=parsed["title"],
                        due_at=due_at,
                        recurrence=parsed.get("recurrence"),
                    )
                    text = formatters.format_reminder(
                        reminder, self.settings.timezone
                    )
                    await update.message.reply_text(f"Reminder set! {text}")
                except Exception as e:
                    logger.error("Failed to create reminder: %s", e)
                    await update.message.reply_text(
                        "Sorry, I couldn't parse that reminder. "
                        "Try something like 'remind me to call doctor tomorrow at 3pm'."
                    )
            else:
                await update.message.reply_text(
                    "I couldn't understand that reminder. Could you rephrase?"
                )
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
```

---

### Step 3F: Bot handlers -- callback handler + conversation context (`src/bot/handlers.py`, part 3 of 3)

**File**: `src/bot/handlers.py` (append to Step 3E)
**Time**: ~5 min
**What**: The `callback_handler` for inline keyboard responses and conversation context management methods (_store_conversation, _get_conversation_context, _maybe_summarize_context).

> **N4 fix:** Uses `Reminder` imported at module top instead of `__import__` hack.
> **N5 fix:** Conversation context methods accept and filter by `user_id`.

Append the following methods to the `BotHandlers` class in `src/bot/handlers.py`:

```python
    # ---- Callback Query Handler (Step 3F) ----

    async def callback_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await auth_check(update, context):
            return

        query = update.callback_query
        await query.answer()
        data = query.data

        if data.startswith("reminder_done:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.complete_reminder(reminder_id)
            await query.edit_message_text("Reminder completed!")

        elif data.startswith("reminder_snooze_1h:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.snooze_reminder(reminder_id, "1h")
            await query.edit_message_text("Snoozed for 1 hour.")

        elif data.startswith("reminder_snooze_tomorrow:"):
            reminder_id = int(data.split(":")[1])
            self.reminders.snooze_reminder(reminder_id, "tomorrow")
            await query.edit_message_text("Snoozed until tomorrow.")

        elif data.startswith("reminder_repeat_weekly:"):
            reminder_id = int(data.split(":")[1])
            # N4: use Reminder imported at module top
            with get_session() as session:
                reminder = session.get(Reminder, reminder_id)
                if reminder:
                    reminder.recurrence = "weekly"
            await query.edit_message_text("Set to repeat weekly!")

        elif data.startswith("email_remind:"):
            email_idx = int(data.split(":")[1])
            self._pending_reminder_context[update.effective_user.id] = {
                "email_index": email_idx,
            }
            await query.edit_message_text(
                "When should I remind you? (e.g., 'tomorrow at 3pm')"
            )

        elif data.startswith("email_done:"):
            await query.edit_message_text("Got it, marked as done.")

        elif data.startswith("email_dismiss:"):
            await query.edit_message_text("Dismissed.")

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
```

**Verify**: `python -c "from src.bot.handlers import BotHandlers; print('Import OK')"` prints `Import OK`.

---

## Group 4: Scheduler

### Step 4A: Scheduler jobs (`src/scheduler/jobs.py`)

**File**: `src/scheduler/jobs.py`
**Time**: ~5 min
**What**: APScheduler job definitions for email checks, reminder delivery, heartbeat, backup, and snoozed reminder wake-up. Integrates services with the Telegram bot for sending messages.
**Depends on**: Group 2 (all services) + Group 3 (bot layer)

> **C2 fix:** `_reminder_check_job` calls `self.reminders.mark_notified(reminder.id)` after sending each notification, preventing infinite re-notification.
> **C4 fix:** `_heartbeat_file_job` writes `/tmp/openear_heartbeat` every 60s for Docker HEALTHCHECK.
> **C5 fix:** `_send_to_all` catches `telegram.error.Forbidden` and logs a clear warning about the `/start` prerequisite.

```python
"""APScheduler job definitions for openEar.

Jobs:
- Email briefing: runs at configured check_times
- Reminder check: runs every minute to fire due reminders
- Heartbeat: runs daily at configured heartbeat_time
- Backup: runs daily at configured backup_time
- Snoozed reminders: runs every minute to wake expired snoozes
- Heartbeat file: writes /tmp/openear_heartbeat every 60s (C4)

C2: _reminder_check_job marks reminders as "notified" after sending.
C4: _heartbeat_file_job writes heartbeat file for Docker HEALTHCHECK.
C5: _send_to_all catches Forbidden errors with clear warning.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram.error import Forbidden
from telegram.ext import Application

from src.bot import formatters, keyboards
from src.config import Settings
from src.services.backup_service import BackupService
from src.services.email_service import EmailService, EmailServiceUnavailable
from src.services.health_service import HealthService
from src.services.llm_service import LLMService
from src.services.reminder_service import ReminderService

logger = logging.getLogger(__name__)

# C4: heartbeat file path for Docker HEALTHCHECK
HEARTBEAT_FILE = "/tmp/openear_heartbeat"


class SchedulerJobs:
    """Manages all scheduled jobs."""

    def __init__(
        self,
        settings: Settings,
        app: Application,
        llm_service: LLMService,
        email_service: EmailService,
        reminder_service: ReminderService,
        health_service: HealthService,
        backup_service: BackupService,
    ) -> None:
        self.settings = settings
        self.app = app
        self.llm = llm_service
        self.email = email_service
        self.reminders = reminder_service
        self.health = health_service
        self.backup = backup_service
        self.scheduler = AsyncIOScheduler()

        # The primary chat ID to send scheduled messages to
        # Uses the first allowed user ID
        self._chat_ids = list(settings.telegram_allowed_user_ids)

    async def _send_to_all(self, text: str, reply_markup=None) -> None:
        """Send a message to all allowed users.

        C5: Catches Forbidden errors (user hasn't sent /start to the bot)
        and logs a clear warning instead of silently failing.
        """
        for chat_id in self._chat_ids:
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            except Forbidden:
                # C5: User hasn't started a conversation with the bot
                logger.warning(
                    "Cannot send to chat_id=%s: user has not sent /start to the bot. "
                    "The bot cannot initiate conversations until the user sends /start.",
                    chat_id,
                )
            except Exception as e:
                logger.error("Failed to send to chat_id=%s: %s", chat_id, e)

    def setup(self) -> None:
        """Register all scheduled jobs."""
        rules = self.settings.rules
        email_config = rules.get("email", {})
        health_config = rules.get("health", {})
        tz = self.settings.timezone

        # Email briefing jobs
        check_times = email_config.get("check_times", ["07:00", "20:00"])
        for check_time in check_times:
            hour, minute = map(int, check_time.split(":"))
            self.scheduler.add_job(
                self._email_briefing_job,
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                id=f"email_check_{check_time}",
                name=f"Email check at {check_time}",
                replace_existing=True,
            )

        # Reminder check - every 60 seconds
        self.scheduler.add_job(
            self._reminder_check_job,
            IntervalTrigger(seconds=60),
            id="reminder_check",
            name="Reminder check",
            replace_existing=True,
        )

        # Snoozed reminder check - every 60 seconds
        self.scheduler.add_job(
            self._snoozed_reminder_job,
            IntervalTrigger(seconds=60),
            id="snoozed_reminder_check",
            name="Snoozed reminder check",
            replace_existing=True,
        )

        # C4: Heartbeat file - every 60 seconds
        self.scheduler.add_job(
            self._heartbeat_file_job,
            IntervalTrigger(seconds=60),
            id="heartbeat_file",
            name="Write heartbeat file",
            replace_existing=True,
        )

        # Daily heartbeat
        heartbeat_time = health_config.get("heartbeat_time", "08:00")
        hb_hour, hb_minute = map(int, heartbeat_time.split(":"))
        self.scheduler.add_job(
            self._heartbeat_job,
            CronTrigger(hour=hb_hour, minute=hb_minute, timezone=tz),
            id="heartbeat",
            name="Daily heartbeat",
            replace_existing=True,
        )

        # Daily S3 backup
        backup_time = health_config.get("backup_time", "03:00")
        bk_hour, bk_minute = map(int, backup_time.split(":"))
        self.scheduler.add_job(
            self._backup_job,
            CronTrigger(hour=bk_hour, minute=bk_minute, timezone=tz),
            id="backup",
            name="Daily S3 backup",
            replace_existing=True,
        )

        # OAuth expiry re-alert (daily at noon if paused)
        self.scheduler.add_job(
            self._oauth_alert_job,
            CronTrigger(hour=12, minute=0, timezone=tz),
            id="oauth_alert",
            name="OAuth expiry re-alert",
            replace_existing=True,
        )

        logger.info("Scheduler jobs registered")

    def start(self) -> None:
        """Start the scheduler."""
        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    # ---- Job Implementations ----

    async def _email_briefing_job(self) -> None:
        """Fetch and send email briefing."""
        logger.info("Running email briefing job")
        try:
            emails = await self.email.process_emails()
            text = formatters.format_briefing(emails, self.settings.timezone)
            await self._send_to_all(text)

            # Send action keyboards for each important email
            for i, email in enumerate(emails):
                if email.get("summary"):
                    await self._send_to_all(
                        f"Actions for: {email['subject']}",
                        reply_markup=keyboards.briefing_actions(i),
                    )
        except EmailServiceUnavailable as e:
            await self._send_to_all(
                f"Email service unavailable: {e}\n"
                "Re-authenticate via scripts/reauth_gmail.py or "
                "scripts/setup_gmail.py."
            )
        except Exception as e:
            logger.error("Email briefing job failed: %s", e)

    async def _reminder_check_job(self) -> None:
        """Check for due reminders and send notifications.

        C2: After sending notification, marks reminder as "notified"
        so it will not be picked up again on the next cycle.
        """
        # Check quiet hours
        if self.reminders.is_quiet_hours():
            return

        due = self.reminders.get_due_reminders()
        for reminder in due:
            text = formatters.format_reminder(reminder, self.settings.timezone)
            await self._send_to_all(
                text, reply_markup=keyboards.reminder_actions(reminder.id)
            )
            # C2: Mark as notified so it won't fire again
            self.reminders.mark_notified(reminder.id)

    async def _snoozed_reminder_job(self) -> None:
        """Re-activate snoozed reminders whose snooze has expired."""
        expired = self.reminders.get_snoozed_due()
        for reminder in expired:
            # Re-activate by setting status back to active
            from src.db.database import get_session
            from src.db.models import Reminder

            with get_session() as session:
                r = session.get(Reminder, reminder.id)
                if r:
                    r.status = "active"

            # It will be picked up by the next reminder_check_job cycle

    async def _heartbeat_file_job(self) -> None:
        """Write heartbeat timestamp to file for Docker HEALTHCHECK.

        C4: The HEALTHCHECK command reads this file and verifies it
        was modified within the last 2 minutes.
        """
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except Exception as e:
            logger.error("Failed to write heartbeat file: %s", e)

    async def _heartbeat_job(self) -> None:
        """Send daily heartbeat to user."""
        logger.info("Sending daily heartbeat")
        groq_status = (
            "CIRCUIT_OPEN" if self.llm.circuit_breaker.is_open else "OK"
        )
        gmail_status = (
            f"DISCONNECTED -- {self.email.pause_reason}"
            if self.email.is_paused
            else "Connected"
        )
        check_times = self.settings.rules.get("email", {}).get(
            "check_times", ["07:00", "20:00"]
        )

        text = self.health.format_heartbeat(
            groq_status=groq_status,
            groq_429_count=self.llm.rate_limit_count_24h,
            gmail_status=gmail_status,
            email_check_times=check_times,
        )
        await self._send_to_all(text)

    async def _backup_job(self) -> None:
        """Run S3 backup."""
        logger.info("Running daily S3 backup")
        success = await self.backup.run_backup()
        if not success:
            await self._send_to_all(
                "S3 backup failed! Check logs for details."
            )

    async def _oauth_alert_job(self) -> None:
        """Re-alert if email service is paused due to OAuth expiry."""
        if self.email.is_paused:
            await self._send_to_all(
                f"Reminder: Email monitoring is still paused.\n"
                f"Reason: {self.email.pause_reason}\n\n"
                "Please re-authenticate:\n"
                "1. Run scripts/reauth_gmail.py on the server, or\n"
                "2. Run scripts/setup_gmail.py locally and upload the token"
            )
```

**Verify**: `python -c "from src.scheduler.jobs import SchedulerJobs; print('Import OK')"` prints `Import OK`.

---

## Group 5: Main Entry Point

### Step 5A: Main entry point (`src/main.py`)

**File**: `src/main.py`
**Time**: ~5 min
**What**: Wires everything together. Initializes config, database, services, bot, and scheduler. Sets up logging with RotatingFileHandler. Runs the single async process.
**Depends on**: All previous groups

```python
"""Main entry point for openEar.

Initializes and runs the single async Python process:
1. Load configuration
2. Set up logging (console + rotating file)
3. Initialize database
4. Create service instances
5. Build Telegram bot application with handlers
6. Start APScheduler
7. Run bot polling loop

All components share the same async event loop.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram.ext import Application

from src.bot.handlers import BotHandlers
from src.config import load_settings
from src.db.database import init_db
from src.services.backup_service import BackupService
from src.services.email_service import EmailService
from src.services.health_service import HealthService
from src.services.llm_service import LLMService
from src.services.note_service import NoteService
from src.services.reminder_service import ReminderService

logger = logging.getLogger("openear")


def setup_logging(log_level: str, log_dir: str) -> None:
    """Configure logging with console and rotating file handlers."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # Rotating file handler: 10 MB per file, keep 5 files (50 MB total)
    file_handler = RotatingFileHandler(
        log_path / "openear.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)


def main() -> None:
    """Main entry point."""
    # 1. Load configuration
    settings = load_settings()

    # 2. Set up logging
    setup_logging(settings.log_level, settings.log_dir)
    logger.info("Starting openEar...")

    # Validate required settings
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)
    if not settings.groq_api_key:
        logger.error("GROQ_API_KEY is not set. Exiting.")
        sys.exit(1)
    if not settings.telegram_allowed_user_ids:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS is empty -- all users will be rejected")

    # 3. Initialize database
    init_db(settings.db_path)

    # 4. Create service instances (in-memory, no external connections yet)
    llm_service = LLMService(settings)
    email_service = EmailService(settings, llm_service)
    reminder_service = ReminderService(settings)
    note_service = NoteService()
    health_service = HealthService(settings)
    backup_service = BackupService(settings)

    # 5. Build Telegram bot application
    app = Application.builder().token(settings.telegram_bot_token).build()

    # Store allowed user IDs in bot_data for auth_check
    app.bot_data["allowed_user_ids"] = settings.telegram_allowed_user_ids

    # Register handlers
    bot_handlers = BotHandlers(
        settings=settings,
        llm_service=llm_service,
        email_service=email_service,
        reminder_service=reminder_service,
        note_service=note_service,
        health_service=health_service,
    )
    for handler in bot_handlers.get_handlers():
        app.add_handler(handler)

    # 6. Start APScheduler
    from src.scheduler.jobs import SchedulerJobs

    scheduler_jobs = SchedulerJobs(
        settings=settings,
        app=app,
        llm_service=llm_service,
        email_service=email_service,
        reminder_service=reminder_service,
        health_service=health_service,
        backup_service=backup_service,
    )
    scheduler_jobs.setup()
    scheduler_jobs.start()

    logger.info("openEar is running. Press Ctrl+C to stop.")

    # 7. Run bot polling loop (blocks until stopped)
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        scheduler_jobs.shutdown()
        logger.info("openEar stopped.")


if __name__ == "__main__":
    main()
```

**Verify**: `python -c "from src.main import main; print('Import OK')"` prints `Import OK`.

---

## Group 6: Scripts

Steps 6A, 6B, 6C, and 6D are independent of each other and can be implemented in parallel. They depend only on Group 1 (config).

---

### Step 6A: Gmail OAuth setup script (`scripts/setup_gmail.py`)

**File**: `scripts/setup_gmail.py`
**Time**: ~4 min
**What**: Local script to run the Google OAuth2 flow in a browser, generate a refresh token, and save it. Can optionally push the token to SSM Parameter Store.

```python
#!/usr/bin/env python3
"""Gmail OAuth setup script for openEar.

Run this locally on a machine with a web browser to generate the
initial OAuth refresh token for Gmail API access.

Usage:
    python scripts/setup_gmail.py

Prerequisites:
    1. Create a Google Cloud project at console.cloud.google.com
    2. Enable the Gmail API
    3. Create OAuth 2.0 credentials (Desktop application)
    4. Download the credentials JSON file
    5. Set the project to Testing status and add your Google account
       as a test user

The script will:
    1. Open a browser for the OAuth consent flow
    2. Save the resulting token to the configured path
    3. Optionally upload to AWS SSM Parameter Store
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Set up Gmail OAuth for openEar")
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to OAuth client credentials JSON file",
    )
    parser.add_argument(
        "--token-output",
        default="token.json",
        help="Path to save the generated token",
    )
    parser.add_argument(
        "--upload-ssm",
        action="store_true",
        help="Upload token to AWS SSM Parameter Store",
    )
    parser.add_argument(
        "--ssm-region",
        default="us-west-2",
        help="AWS region for SSM (default: us-west-2)",
    )
    args = parser.parse_args()

    if not Path(args.credentials).exists():
        print(f"Error: Credentials file not found: {args.credentials}")
        print(
            "Download it from Google Cloud Console > APIs & Services > Credentials"
        )
        sys.exit(1)

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Error: Required packages not installed.")
        print("Run: pip install google-auth-oauthlib google-auth-httplib2")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    creds = None
    token_path = Path(args.token_output)

    # Check for existing token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
            print("Starting OAuth flow. A browser window will open...")
            flow = InstalledAppFlow.from_client_secrets_file(
                args.credentials, SCOPES
            )
            creds = flow.run_local_server(port=0)

    # Save token locally
    with open(token_path, "w") as f:
        f.write(creds.to_json())
    print(f"Token saved to {token_path}")

    # Verify the token works
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        print(f"Authenticated as: {profile.get('emailAddress')}")
    except Exception as e:
        print(f"Warning: Could not verify token: {e}")

    # Optionally upload to SSM
    if args.upload_ssm:
        try:
            import boto3

            ssm = boto3.client("ssm", region_name=args.ssm_region)

            # Store refresh token
            token_data = json.loads(creds.to_json())
            ssm.put_parameter(
                Name="/openear/gmail/refresh_token",
                Value=token_data.get("refresh_token", ""),
                Type="SecureString",
                Overwrite=True,
            )
            print("Refresh token uploaded to SSM: /openear/gmail/refresh_token")

        except Exception as e:
            print(f"Error uploading to SSM: {e}")
            sys.exit(1)

    print("\nSetup complete!")


if __name__ == "__main__":
    main()
```

**Verify**: `python scripts/setup_gmail.py --help` prints the usage message without errors.

---

### Step 6B: Deploy script (`scripts/deploy.sh`)

**File**: `scripts/deploy.sh`
**Time**: ~3 min
**What**: Tags previous commit SHA before pulling, builds, and verifies logs. Includes rollback instructions.

```bash
#!/usr/bin/env bash
# openEar deploy script
#
# Usage: ./scripts/deploy.sh
#
# This script:
# 1. Tags the current commit SHA for rollback
# 2. Pulls the latest code
# 3. Rebuilds and restarts the Docker container
# 4. Verifies startup via log inspection
# 5. Prints rollback instructions if verification fails

set -euo pipefail

COMPOSE_FILE="docker-compose.yml"
LOG_WAIT_SECONDS=15
SUCCESS_PATTERN="openEar is running"

echo "=== openEar Deploy ==="
echo "$(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# Step 1: Tag current state for rollback
PREV_SHA=$(git rev-parse HEAD)
echo "Previous SHA: ${PREV_SHA}"
echo ""

# Step 2: Pull latest code
echo "Pulling latest code..."
git pull
NEW_SHA=$(git rev-parse HEAD)
echo "New SHA: ${NEW_SHA}"

if [ "${PREV_SHA}" = "${NEW_SHA}" ]; then
    echo "No new changes. Rebuilding anyway..."
fi
echo ""

# Step 3: Rebuild and restart
echo "Building and starting container..."
docker compose -f "${COMPOSE_FILE}" up -d --build
echo ""

# Step 4: Wait and verify startup
echo "Waiting ${LOG_WAIT_SECONDS}s for startup..."
sleep "${LOG_WAIT_SECONDS}"

echo "Checking logs..."
LOGS=$(docker compose -f "${COMPOSE_FILE}" logs --tail=30 2>&1)
echo "${LOGS}"
echo ""

if echo "${LOGS}" | grep -q "${SUCCESS_PATTERN}"; then
    echo "=== Deploy successful! ==="
else
    echo "=== WARNING: Success pattern not found in logs ==="
    echo ""
    echo "The container may still be starting. Check with:"
    echo "  docker compose logs -f --tail=50"
    echo ""
    echo "To rollback:"
    echo "  git checkout ${PREV_SHA}"
    echo "  docker compose up -d --build"
fi
```

**Verify**: `bash -n scripts/deploy.sh` exits with 0 (syntax check only, does not execute).

---

### Step 6C: CloudWatch metrics push script (`scripts/push_cloudwatch_metrics.sh`)

**File**: `scripts/push_cloudwatch_metrics.sh`
**Time**: ~3 min
**What**: Cron script that pushes disk usage, memory usage, and container liveness to CloudWatch custom metrics every 5 minutes.

```bash
#!/usr/bin/env bash
# Push custom CloudWatch metrics for openEar monitoring.
#
# Run via cron every 5 minutes:
#   */5 * * * * /path/to/push_cloudwatch_metrics.sh
#
# Pushes:
#   - DiskUsagePercent: percentage of disk used on root filesystem
#   - MemoryUsagePercent: percentage of memory used
#   - ContainerRunning: 1 if openear container is running, 0 if not

set -euo pipefail

NAMESPACE="openEar"
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || echo "local")
REGION="${AWS_REGION:-us-west-2}"

# Disk usage (root filesystem)
DISK_USED_PCT=$(df / | awk 'NR==2 {print $5}' | tr -d '%')

# Memory usage
MEM_TOTAL=$(free -m | awk '/^Mem:/ {print $2}')
MEM_USED=$(free -m | awk '/^Mem:/ {print $3}')
MEM_PCT=$(( MEM_USED * 100 / MEM_TOTAL ))

# Container liveness
CONTAINER_RUNNING=0
if docker inspect --format='{{.State.Running}}' openear 2>/dev/null | grep -q "true"; then
    CONTAINER_RUNNING=1
fi

# Push metrics
aws cloudwatch put-metric-data \
    --region "${REGION}" \
    --namespace "${NAMESPACE}" \
    --metric-data \
        "MetricName=DiskUsagePercent,Value=${DISK_USED_PCT},Unit=Percent,Dimensions=[{Name=InstanceId,Value=${INSTANCE_ID}}]" \
        "MetricName=MemoryUsagePercent,Value=${MEM_PCT},Unit=Percent,Dimensions=[{Name=InstanceId,Value=${INSTANCE_ID}}]" \
        "MetricName=ContainerRunning,Value=${CONTAINER_RUNNING},Unit=None,Dimensions=[{Name=InstanceId,Value=${INSTANCE_ID}}]"

echo "$(date '+%Y-%m-%d %H:%M:%S') Pushed metrics: disk=${DISK_USED_PCT}% mem=${MEM_PCT}% container=${CONTAINER_RUNNING}"
```

**Verify**: `bash -n scripts/push_cloudwatch_metrics.sh` exits with 0 (syntax check only).

---

### Step 6D: Gmail re-auth script with Flask (`scripts/reauth_gmail.py`)

**File**: `scripts/reauth_gmail.py`
**Time**: ~5 min
**What**: Temporary Flask endpoint for headless OAuth re-authentication on EC2. Includes automatic 10-minute timeout and random URL nonce for security.

> **C9 fix:** This script was missing from the original plan. It implements the "Option A -- Temporary Flask endpoint" described in the design doc, with the timeout and nonce requirements from Design Review 2 C2.

```python
#!/usr/bin/env python3
"""Gmail OAuth re-authentication script for headless EC2.

Starts a temporary Flask server with a random URL nonce for security.
The server automatically shuts down after 10 minutes if no auth
completes. Once the OAuth flow succeeds, the new refresh token is
saved to SSM Parameter Store and the Flask server stops.

Usage:
    python scripts/reauth_gmail.py --port 8443

Prerequisites:
    - Security group temporarily allows inbound on the chosen port
    - pip install flask google-auth-oauthlib boto3

C9: This script was missing from the original plan.
Design Review 2 C2: Requires automatic timeout and URL nonce.
"""

import argparse
import json
import logging
import os
import secrets
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Auto-shutdown timeout in seconds (10 minutes)
AUTO_SHUTDOWN_SECONDS = 600


def main():
    parser = argparse.ArgumentParser(
        description="Temporary Flask server for Gmail OAuth re-auth"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8443,
        help="Port to run Flask on (default: 8443)",
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to OAuth client credentials JSON file",
    )
    parser.add_argument(
        "--ssm-region",
        default=os.getenv("AWS_REGION", "us-west-2"),
        help="AWS region for SSM",
    )
    args = parser.parse_args()

    if not Path(args.credentials).exists():
        print(f"Error: Credentials file not found: {args.credentials}")
        sys.exit(1)

    try:
        from flask import Flask, redirect, request, session
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        print("Error: Required packages not installed.")
        print("Run: pip install flask google-auth-oauthlib")
        sys.exit(1)

    # Generate random nonce for URL security
    nonce = secrets.token_urlsafe(16)

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    completed = threading.Event()

    @app.route(f"/reauth/{nonce}")
    def start_auth():
        """Start the OAuth flow."""
        flow = Flow.from_client_secrets_file(
            args.credentials,
            scopes=SCOPES,
            redirect_uri=f"http://localhost:{args.port}/callback/{nonce}",
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        session["state"] = state
        return redirect(auth_url)

    @app.route(f"/callback/{nonce}")
    def callback():
        """Handle the OAuth callback."""
        state = session.get("state")
        flow = Flow.from_client_secrets_file(
            args.credentials,
            scopes=SCOPES,
            state=state,
            redirect_uri=f"http://localhost:{args.port}/callback/{nonce}",
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials

        # Save to SSM
        try:
            import boto3

            ssm = boto3.client("ssm", region_name=args.ssm_region)
            token_data = json.loads(creds.to_json())
            ssm.put_parameter(
                Name="/openear/gmail/refresh_token",
                Value=token_data.get("refresh_token", ""),
                Type="SecureString",
                Overwrite=True,
            )
            logger.info("Refresh token saved to SSM")
        except Exception as e:
            logger.error("Failed to save to SSM: %s", e)
            return f"Error saving token: {e}", 500

        # Also save locally as backup
        token_path = Path("token.json")
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info("Token also saved locally to %s", token_path)

        completed.set()

        return (
            "<h1>Re-authentication successful!</h1>"
            "<p>The new Gmail token has been saved. "
            "You can close this tab. The server will shut down automatically.</p>"
        )

    # Auto-shutdown timer
    def auto_shutdown():
        if not completed.wait(timeout=AUTO_SHUTDOWN_SECONDS):
            logger.warning(
                "Auto-shutdown: no auth completed within %d seconds",
                AUTO_SHUTDOWN_SECONDS,
            )
            os._exit(0)
        else:
            # Give a moment for the response to be sent
            import time

            time.sleep(2)
            logger.info("Auth completed, shutting down Flask server")
            os._exit(0)

    shutdown_thread = threading.Thread(target=auto_shutdown, daemon=True)
    shutdown_thread.start()

    print(f"\n{'=' * 60}")
    print(f"Gmail re-auth server starting on port {args.port}")
    print(f"URL: http://<your-ec2-ip>:{args.port}/reauth/{nonce}")
    print(f"This server will auto-shutdown in {AUTO_SHUTDOWN_SECONDS // 60} minutes.")
    print(f"{'=' * 60}\n")
    print(
        "IMPORTANT: Ensure your security group allows inbound "
        f"TCP on port {args.port} from your IP."
    )
    print()

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
```

**Verify**: `python scripts/reauth_gmail.py --help` prints the usage message without errors.

---

## Group 7: Docker and Deployment Verification

### Step 7A: Update Docker files and verify build

**Time**: ~5 min
**What**: Update the Dockerfile and docker-compose.yml for production readiness. Verify the image builds successfully.

> **C4 fix:** HEALTHCHECK now verifies `/tmp/openear_heartbeat` was modified within the last 120 seconds.
> **C8 fix:** `.env.example` split into dev guidance (all values) and prod guidance (non-sensitive only). Production docker-compose does not load secrets via env_file.
> **C10 fix:** Added note about configuring CloudWatch alarm on `ContainerRunning == 0`.
> **N13 fix:** `boto3` is included in `requirements.txt`.

**File**: `Dockerfile` (update existing)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for potential native packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create directories for data and logs
RUN mkdir -p /app/data /app/logs

# C4: HEALTHCHECK verifies the application is alive by checking the
# heartbeat file written every 60s by the scheduler. The file must
# have been modified within the last 120 seconds.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, time; s=os.stat('/tmp/openear_heartbeat'); exit(0 if time.time()-s.st_mtime<120 else 1)"

CMD ["python", "-m", "src.main"]
```

**File**: `docker-compose.yml` (update existing)

```yaml
services:
  openear:
    build: .
    container_name: openear
    restart: unless-stopped
    # C8: In production, this .env contains ONLY non-sensitive values
    # (LOG_LEVEL, AWS_REGION, GROQ_MODEL). Secrets come from SSM at
    # startup via boto3. For local dev, .env may include secrets.
    env_file: .env
    volumes:
      - openear-data:/app/data
      - openear-logs:/app/logs
      - ./config:/app/config:ro
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    environment:
      - TZ=America/Los_Angeles

volumes:
  openear-data:
  openear-logs:
```

**File**: `requirements.txt` (update existing -- add missing dependencies)

```
python-telegram-bot>=20.0
groq>=0.4.0
google-api-python-client>=2.0
google-auth-httplib2>=0.1.0
google-auth-oauthlib>=1.0
sqlalchemy>=2.0
apscheduler>=3.10
pyyaml>=6.0
yfinance>=0.2
duckduckgo-search>=5.0
httpx>=0.27
python-dotenv>=1.0
boto3>=1.34
```

**File**: `.env.example` (update existing)

```
# === openEar Configuration ===
#
# PRODUCTION: This file should contain ONLY non-sensitive config.
# Secrets (API keys, tokens) come from AWS SSM Parameter Store.
# Copy this file to .env and fill in the values below.
#
# LOCAL DEV: You may add secrets here for convenience. They will be
# overridden by SSM values if SSM is reachable.

# --- Non-sensitive (always in .env) ---
GROQ_MODEL=llama3-70b-8192
AWS_REGION=us-west-2
LOG_LEVEL=INFO
LOG_DIR=/app/logs

# --- Sensitive (LOCAL DEV ONLY -- do NOT set these in production .env) ---
# In production, these come from SSM Parameter Store.
# TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
# TELEGRAM_ALLOWED_USER_IDS=your-telegram-user-id
# GROQ_API_KEY=your-groq-api-key

# --- Gmail OAuth (generated by scripts/setup_gmail.py) ---
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
```

> **C10 note -- CloudWatch alarm for container liveness:** After deploying, configure a CloudWatch alarm on the `ContainerRunning` metric (namespace: `openEar`) with the condition `ContainerRunning == 0 for > 5 minutes`. This triggers an SNS notification when the container has been down for 5+ minutes. Set this up via the AWS Console or CLI:
> ```bash
> aws cloudwatch put-metric-alarm \
>     --alarm-name "openear-container-down" \
>     --namespace "openEar" \
>     --metric-name "ContainerRunning" \
>     --statistic "Minimum" \
>     --period 300 \
>     --evaluation-periods 1 \
>     --threshold 1 \
>     --comparison-operator "LessThanThreshold" \
>     --dimensions "Name=InstanceId,Value=<your-instance-id>" \
>     --alarm-actions "<your-sns-topic-arn>"
> ```

**Verify**:
```bash
docker compose build --no-cache
docker compose config  # validates the compose file
```

---

## Group 8: End-to-End Testing

### Step 8A: Local end-to-end smoke test

**Time**: ~5 min
**What**: Run the bot locally (not in Docker) to verify all components wire together. Requires valid `.env` with at minimum `TELEGRAM_BOT_TOKEN` and `GROQ_API_KEY`.

**Pre-requisites**: Copy `.env.example` to `.env` and fill in real values for `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, and `TELEGRAM_ALLOWED_USER_IDS`.

> **C5 prerequisite:** Before running scheduled jobs, you MUST send `/start` to the bot from each Telegram account listed in `TELEGRAM_ALLOWED_USER_IDS`. The Telegram Bot API requires the user to initiate a conversation before the bot can send proactive messages (briefings, reminders, heartbeats). Without this, all scheduled messages will fail with a `Forbidden` error. The error is logged clearly (see C5 fix in `_send_to_all`).

**Test procedure** (manual):

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the bot locally
python -m src.main
# Expected: "openEar is running. Press Ctrl+C to stop."

# 3. In Telegram, send /start to your bot FIRST (C5 prerequisite)

# 4. In Telegram, send these messages to your bot:
#    /start           -> should get welcome message
#    /help            -> should get command list
#    /status          -> should get health status
#    "what's the weather?" -> should get weather data
#    "how's AAPL?"    -> should get stock quote
#    /note pick up groceries -> should save note
#    /notes           -> should list saved note
#    "remind me to call doctor tomorrow at 3pm" -> should set reminder
#    /reminders       -> should list the reminder
#    /briefing        -> should attempt email check (may fail without Gmail token)

# 5. Verify logs
#    Check logs/openear.log for any ERROR entries

# 6. Verify database
python -c "
from src.db.database import init_db, get_session
from src.db.models import Note, Reminder, Conversation
init_db('data/openear.db')
with get_session() as s:
    notes = s.query(Note).all()
    reminders = s.query(Reminder).all()
    convos = s.query(Conversation).all()
    print(f'Notes: {len(notes)}, Reminders: {len(reminders)}, Conversations: {len(convos)}')
"

# 7. Stop the bot with Ctrl+C
# Expected: "openEar stopped."
```

**Verification checklist**:

| # | Test | Expected Result |
|---|------|----------------|
| 1 | Bot starts without errors | "openEar is running" in console |
| 2 | /start command | Welcome message with feature list |
| 3 | /status command | Health status with uptime, DB size, etc. |
| 4 | Weather query | Temperature and forecast data |
| 5 | Stock query | Price and change percentage |
| 6 | /note command | Note saved confirmation |
| 7 | /notes command | List showing the saved note |
| 8 | Natural language reminder | Reminder set confirmation |
| 9 | /reminders command | List showing the created reminder |
| 10 | /briefing command | Email check attempt (OK to fail without Gmail token) |
| 11 | Unauthorized user message | Silently dropped, logged as warning |
| 12 | General conversation | LLM-powered response matching persona |
| 13 | Logs file exists | logs/openear.log contains startup entries |
| 14 | Database file exists | data/openear.db contains tables |
| 15 | Heartbeat file exists | /tmp/openear_heartbeat is updated every 60s (C4) |
| 16 | Docker HEALTHCHECK | `docker inspect --format='{{.State.Health.Status}}'` returns "healthy" (C4) |

---

## Summary of All Files

| File | Group | Step | Action |
|------|-------|------|--------|
| `src/__init__.py` | -- | -- | Already exists (C3: do not overwrite) |
| `src/bot/__init__.py` | -- | -- | Already exists (C3: do not overwrite) |
| `src/db/__init__.py` | -- | -- | Already exists (C3: do not overwrite) |
| `src/services/__init__.py` | -- | -- | Already exists (C3: do not overwrite) |
| `src/scheduler/__init__.py` | -- | -- | Already exists (C3: do not overwrite) |
| `src/config.py` | 1 | 1A | Create |
| `src/db/models.py` | 1 | 1B | Create |
| `src/db/database.py` | 1 | 1C | Create |
| `src/services/llm_service.py` | 2 | 2A | Create |
| `src/services/email_service.py` | 2 | 2B | Create |
| `src/services/reminder_service.py` | 2 | 2C | Create |
| `src/services/note_service.py` | 2 | 2D | Create |
| `src/services/info_service.py` | 2 | 2E | Create |
| `src/services/health_service.py` | 2 | 2F | Create |
| `src/services/backup_service.py` | 2 | 2G | Create |
| `src/auth.py` | 3 | 3A | Create |
| `src/bot/keyboards.py` | 3 | 3B | Create |
| `src/bot/formatters.py` | 3 | 3C | Create |
| `src/bot/handlers.py` | 3 | 3D+3E+3F | Create (3 steps, 1 file) |
| `src/scheduler/jobs.py` | 4 | 4A | Create |
| `src/main.py` | 5 | 5A | Create |
| `scripts/setup_gmail.py` | 6 | 6A | Create |
| `scripts/deploy.sh` | 6 | 6B | Create |
| `scripts/push_cloudwatch_metrics.sh` | 6 | 6C | Create |
| `scripts/reauth_gmail.py` | 6 | 6D | Create (C9) |
| `Dockerfile` | 7 | 7A | Update |
| `docker-compose.yml` | 7 | 7A | Update |
| `requirements.txt` | 7 | 7A | Update |
| `.env.example` | 7 | 7A | Update |

**Total steps**: 23 (across 8 groups)
**Estimated time**: 85-100 minutes of focused implementation
**Maximum parallelism**: Group 1 (3 parallel), Group 2 (7 parallel), Group 3 (3 parallel + 3 sequential), Group 6 (4 parallel)
