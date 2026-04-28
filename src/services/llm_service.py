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
import re
import time
from dataclasses import dataclass, field

from openai import AsyncOpenAI, RateLimitError

from src.config import Settings


def _clean_json(text: str) -> str:
    """Strip markdown code fences and whitespace from LLM JSON output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()
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
    """Async LLM service with multi-provider support, retry, and circuit breaker."""

    PROVIDER_CONFIG = {
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "key_field": "groq_api_key",
        },
        "cohere": {
            "base_url": "https://api.cohere.com/compatibility/v1",
            "key_field": "cohere_api_key",
        },
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        provider = settings.llm_provider
        config = self.PROVIDER_CONFIG.get(provider, self.PROVIDER_CONFIG["cohere"])
        api_key = getattr(settings, config["key_field"], "")
        self.client = AsyncOpenAI(api_key=api_key, base_url=config["base_url"])
        self.model = settings.llm_model
        self.circuit_breaker = CircuitBreaker()
        self._rate_limit_count_24h: list[float] = []
        self._last_successful_call: float | None = None
        logger.info("LLM provider: %s, model: %s", provider, self.model)

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
                content = response.choices[0].message.content
                return re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip() if content else content

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
        intent is one of: "reminder", "modify", "note", "weather",
        "stock", "news", "email", "status", "general".

        For "modify" intent, also returns "action" key with one of:
        "reschedule", "cancel", "change_alert".
        """
        system_prompt = """You are an intent classifier. Given a user message, classify it.

If the message contains MULTIPLE requests, return a JSON ARRAY of objects.
If it contains ONE request, return a single JSON object.

Intents:
- "reminder": set a NEW reminder
- "modify": change/reschedule/cancel an EXISTING reminder
- "note": save a note or piece of information
- "weather": asks about weather
- "stock": asks about stocks
- "news": asks about news
- "whitelist": add/remove email sender to whitelist
- "setup": user wants to configure bot settings (email check times, stock watchlist, quiet hours, preferences)
- "general": general conversation

For "modify": include "action": "reschedule|cancel|change_alert"
For "whitelist": include "email" and "label" fields
For "setup": include "settings" array with {"key": "<setting_key>", "value": "<new_value>"} objects

Setup setting keys:
- "email_check_morning": morning email check time (HH:MM format, e.g. "06:30")
- "email_check_evening": evening email check time (HH:MM format, e.g. "21:30")
- "stock_symbols": comma-separated stock symbols to watch (e.g. "META,AAPL,TSLA")
- "stock_symbols_add": single stock symbol to ADD to watchlist (e.g. "TSLA")
- "stock_symbols_remove": single stock symbol to REMOVE from watchlist (e.g. "META")
- "quiet_start": quiet hours start time (HH:MM, e.g. "22:00")
- "quiet_end": quiet hours end time (HH:MM, e.g. "07:00")

Single request format:
{"intent": "<intent>", "content": "<extracted content>", "action": "<if modify>", "tags": ["<tag>"]}

Setup format:
{"intent": "setup", "content": "<what to configure>", "settings": [{"key": "email_check_morning", "value": "06:30"}], "tags": []}

Multiple requests format:
[{"intent": "whitelist", "content": "add doctor@clinic.com", "email": "doctor@clinic.com", "label": "Medical", "tags": []},
 {"intent": "note", "content": "Aaron is allergic to eggs and dairy", "tags": ["allergy", "Aaron"]}]"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        result = await self.call_groq(messages, temperature=0.1)
        if result is None:
            return {"intent": "general", "content": user_message, "tags": []}

        try:
            return json.loads(_clean_json(result))
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
            parsed = json.loads(_clean_json(result))
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

    async def parse_reminder_time(self, user_input: str, timezone: str = "America/Los_Angeles") -> dict | None:
        """Parse natural language time into structured reminder data.

        Uses dateparser for reliable date/time extraction, LLM only for title.
        Returns dict with keys: title, due_at (ISO format), recurrence (optional).
        """
        import re as _re

        import dateparser
        from zoneinfo import ZoneInfo

        system_prompt = """Extract reminder details from the user's message. Return ONLY a JSON object:
{"title": "<what to remind about>", "time_phrase": "<the time/date part>", "recurrence": "<daily|weekly|monthly|null>", "pre_alerts": [<list of alert preferences>]}

pre_alerts should contain objects like:
- {"offset": "1d_before", "time": "20:00", "label": "Tomorrow"} — remind the evening before
- {"offset": "morning_of", "time": "08:00", "label": "Today"} — remind the morning of
- {"offset": "1h_before", "label": "In 1 hour"} — remind 1 hour before
- {"offset": "2d_before", "time": "20:00", "label": "In 2 days"} — remind 2 days before

If the user specifies when to be alerted (e.g. "remind me the day before and morning of"), extract those into pre_alerts.
If the user does NOT specify alert preferences, set pre_alerts to "ask" so the bot can ask them.

Examples:
- "remind me couple therapy Monday 4pm, alert me day before and morning of" -> {"title": "Couple therapy", "time_phrase": "Monday at 4pm", "recurrence": null, "pre_alerts": [{"offset": "1d_before", "time": "20:00", "label": "Tomorrow"}, {"offset": "morning_of", "time": "08:00", "label": "Today"}]}
- "remind me buy groceries tomorrow 5pm" -> {"title": "Buy groceries", "time_phrase": "tomorrow at 5pm", "recurrence": null, "pre_alerts": "ask"}
- "remind me take vitamins every day 9am, just remind me at the time" -> {"title": "Take vitamins", "time_phrase": "every day at 9am", "recurrence": "daily", "pre_alerts": []}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
        result = await self.call_groq(messages, temperature=0.1)
        if result is None:
            return None
        try:
            parsed = json.loads(_clean_json(result))
        except json.JSONDecodeError:
            logger.warning("Failed to parse reminder JSON: %s", result)
            return None

        time_phrase = parsed.get("time_phrase", "")
        normalized = _re.sub(r"\b(next|this|coming)\b", "", time_phrase, flags=_re.IGNORECASE).strip()
        normalized = _re.sub(r"\bfrom\s+(\d{1,2}(?::\d{2})?(?:\s*[ap]m)?)\s+to\s+\d{1,2}(?::\d{2})?(?:\s*[ap]m)?", r"at \1", normalized, flags=_re.IGNORECASE).strip()

        dt = dateparser.parse(
            normalized,
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
            },
        )
        if dt is None:
            dt = dateparser.parse(
                time_phrase,
                settings={
                    "PREFER_DATES_FROM": "future",
                    "TIMEZONE": timezone,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                },
            )
        if dt is None:
            logger.warning("dateparser failed for: %s", time_phrase)
            return None

        return {
            "title": parsed.get("title", user_input),
            "due_at": dt.isoformat(),
            "recurrence": parsed.get("recurrence"),
            "pre_alerts": parsed.get("pre_alerts", "ask"),
        }

    async def parse_modification(
        self, user_message: str, reminders: list[dict], timezone_str: str = "America/Los_Angeles"
    ) -> dict | None:
        """Determine which reminder to modify and how, given user message and active reminders.

        Args:
            user_message: the user's natural language modification request
            reminders: list of dicts with keys: id, title, due_at (ISO string)
            timezone_str: user's timezone

        Returns:
            dict with keys: reminder_id, action (reschedule|cancel|change_alert),
            new_time (ISO datetime string if reschedule), details (any extra info).
            Returns None if LLM is unavailable or parsing fails.
        """
        import dateparser
        from zoneinfo import ZoneInfo

        reminder_lines = []
        for r in reminders:
            reminder_lines.append(f"  ID={r['id']}: \"{r['title']}\" due at {r['due_at']}")
        reminder_list_str = "\n".join(reminder_lines) if reminder_lines else "  (none)"

        system_prompt = f"""You are a reminder modification assistant. The user wants to modify an existing reminder.

Here are the user's current active reminders:
{reminder_list_str}

Based on the user's message, determine:
1. Which reminder they are referring to (best match by title/description)
2. What action to take: "reschedule" (change time), "cancel" (delete it), or "change_alert" (modify alert settings)
3. If rescheduling, extract the new time phrase

Return ONLY a JSON object:
{{"reminder_id": <id of best matching reminder>, "action": "<reschedule|cancel|change_alert>", "time_phrase": "<new time/date phrase if rescheduling, else null>", "details": "<any additional info>"}}

If no reminder matches at all, return:
{{"reminder_id": null, "action": "none", "time_phrase": null, "details": "No matching reminder found"}}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        result = await self.call_groq(messages, temperature=0.1)
        if result is None:
            return None

        try:
            parsed = json.loads(_clean_json(result))
        except json.JSONDecodeError:
            logger.warning("Failed to parse modification JSON: %s", result)
            return None

        # If rescheduling, resolve the time_phrase to an ISO datetime
        if parsed.get("action") == "reschedule" and parsed.get("time_phrase"):
            import re as _re

            time_phrase = parsed["time_phrase"]
            normalized = _re.sub(r"\b(next|this|coming)\b", "", time_phrase, flags=_re.IGNORECASE).strip()

            dt = dateparser.parse(
                normalized,
                settings={
                    "PREFER_DATES_FROM": "future",
                    "TIMEZONE": timezone_str,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                },
            )
            if dt is None:
                dt = dateparser.parse(
                    time_phrase,
                    settings={
                        "PREFER_DATES_FROM": "future",
                        "TIMEZONE": timezone_str,
                        "RETURN_AS_TIMEZONE_AWARE": True,
                    },
                )
            if dt is not None:
                parsed["new_time"] = dt.isoformat()
            else:
                parsed["new_time"] = None
                logger.warning("dateparser failed for modification time: %s", time_phrase)
        else:
            parsed["new_time"] = None

        return parsed

    async def parse_alert_time(
        self, user_input: str, event_time: "datetime", timezone_str: str = "America/Los_Angeles"
    ) -> "list[dict] | None":
        """Parse custom alert times. Returns list of {datetime, label} dicts.

        Supports compound requests like 'morning of the event and the night before'.
        LLM understands language, code does math.
        """
        from datetime import datetime as dt_type
        from datetime import timedelta, timezone as tz
        from zoneinfo import ZoneInfo

        now_utc = dt_type.now(tz.utc)
        now_local = now_utc.astimezone(ZoneInfo(timezone_str))
        event_local = event_time.astimezone(ZoneInfo(timezone_str))

        system_prompt = f"""Parse the user's alert time request. The user may request ONE or MULTIPLE alerts.
Return a JSON ARRAY of objects (even for a single alert).

Each object must be one of these formats:
1. Relative to NOW: {{"type": "from_now", "minutes": <number>, "label": "<description>"}}
2. Relative to EVENT: {{"type": "before_event", "minutes": <number>, "label": "<description>"}}
3. Specific time: {{"type": "absolute", "date": "<YYYY-MM-DD>", "time": "<HH:MM>", "timezone": "{timezone_str}", "label": "<description>"}}

TIME-OF-DAY DEFINITIONS:
- "morning" = 08:00
- "afternoon" = 14:00
- "evening" = 18:00
- "night" = 20:00

Examples:
- "morning of the event" -> [{{"type": "absolute", "date": "{event_local.strftime('%Y-%m-%d')}", "time": "08:00", "timezone": "{timezone_str}", "label": "Morning of"}}]
- "the night before" -> [{{"type": "absolute", "date": "{(event_local - timedelta(days=1)).strftime('%Y-%m-%d')}", "time": "20:00", "timezone": "{timezone_str}", "label": "Night before"}}]
- "morning of the event and the night before" -> [{{"type": "absolute", "date": "{event_local.strftime('%Y-%m-%d')}", "time": "08:00", "timezone": "{timezone_str}", "label": "Morning of"}}, {{"type": "absolute", "date": "{(event_local - timedelta(days=1)).strftime('%Y-%m-%d')}", "time": "20:00", "timezone": "{timezone_str}", "label": "Night before"}}]
- "1 minute from now" -> [{{"type": "from_now", "minutes": 1, "label": "Test alert"}}]
- "1 hour before" -> [{{"type": "before_event", "minutes": 60, "label": "1 hour before"}}]

IMPORTANT - NOT an alert time (return empty array []):
- If the message describes a NEW event/task (has a title like "remind me to...", "meet therapist", "call doctor"), return []
- Alert times are ONLY about WHEN to notify, not WHAT to do
- "remind me 1 hour before" = alert time (WHEN to notify)
- "remind me to call doctor Monday 3pm" = NEW event (NOT an alert time, return [])
- "next Monday at 3pm" with no event description = alert time
- "meet therapist Monday 3pm" = NEW event (return [])

CONTEXT:
- Now: {now_local.strftime("%A %B %d, %Y %I:%M %p")} ({timezone_str})
- Event being configured: "{event_local.strftime('%A %B %d, %Y %I:%M %p')}"
- We are setting NOTIFICATION times for this event, not creating new events

Return ONLY the JSON array, nothing else. Return [] if input is not an alert time."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
        result = await self.call_groq(messages, temperature=0.1)
        if not result:
            return None

        try:
            parsed_list = json.loads(_clean_json(result))
        except json.JSONDecodeError:
            cleaned = result.strip()
            if not cleaned.startswith("["):
                cleaned = f"[{cleaned}]"
            try:
                parsed_list = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("Failed to parse alert time JSON: %s", result)
                return None

        alerts = []
        for parsed in parsed_list:
            try:
                alert_type = parsed.get("type")
                label = parsed.get("label", "Custom")
                dt_result = None
                if alert_type == "from_now":
                    minutes = int(parsed.get("minutes", 0))
                    dt_result = now_utc + timedelta(minutes=minutes)
                elif alert_type == "before_event":
                    minutes = int(parsed.get("minutes", 0))
                    dt_result = event_time - timedelta(minutes=minutes)
                elif alert_type == "absolute":
                    date_str = parsed.get("date", "")
                    time_str = parsed.get("time", "08:00")
                    tz_str = parsed.get("timezone", timezone_str)
                    local_dt = dt_type.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    local_dt = local_dt.replace(tzinfo=ZoneInfo(tz_str))
                    dt_result = local_dt.astimezone(tz.utc)

                if dt_result:
                    alerts.append({"datetime": dt_result, "label": label})
            except Exception as e:
                logger.warning("Failed to compute alert time: %s (parsed: %s)", e, parsed)

        return alerts if alerts else None

    @staticmethod
    def validate_setup_settings(settings: list[dict]) -> tuple[list[dict], list[str]]:
        """Validate parsed setup settings.

        Returns (valid_settings, errors) where valid_settings is a list
        of validated {key, value} dicts and errors is a list of
        human-readable error messages.
        """
        import re as _re

        valid = []
        errors = []
        time_pattern = _re.compile(r"^\d{2}:\d{2}$")
        symbol_pattern = _re.compile(r"^[A-Z]{1,5}$")

        for s in settings:
            key = s.get("key", "")
            value = s.get("value", "")

            if key in ("email_check_morning", "email_check_evening",
                       "quiet_start", "quiet_end"):
                if not time_pattern.match(value):
                    errors.append(f"Invalid time format for {key}: '{value}' (expected HH:MM)")
                    continue
                parts = value.split(":")
                h, m = int(parts[0]), int(parts[1])
                if h > 23 or m > 59:
                    errors.append(f"Invalid time for {key}: '{value}'")
                    continue
                valid.append({"key": key, "value": value})

            elif key in ("stock_symbols_add", "stock_symbols_remove"):
                sym = value.upper().strip()
                if not symbol_pattern.match(sym):
                    errors.append(f"Invalid stock symbol: '{value}'")
                    continue
                valid.append({"key": key, "value": sym})

            elif key == "stock_symbols":
                syms = [x.strip().upper() for x in value.split(",") if x.strip()]
                bad = [x for x in syms if not symbol_pattern.match(x)]
                if bad:
                    errors.append(f"Invalid stock symbols: {', '.join(bad)}")
                    continue
                valid.append({"key": key, "value": ",".join(syms)})

            else:
                errors.append(f"Unknown setting key: '{key}'")

        return valid, errors
