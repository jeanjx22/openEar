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
        import re as _re
        email_match = _re.search(r'<([^>]+)>', sender)
        email_only = email_match.group(1).lower() if email_match else sender.lower()
        sender_lower = sender.lower()

        for entry in whitelist:
            pattern = entry.pattern.lower()
            if fnmatch.fnmatch(email_only, pattern) or fnmatch.fnmatch(sender_lower, pattern):
                return entry.label
        return None

    async def fetch_email_body(self, gmail_id: str) -> str | None:
        """Fetch a single email body from Gmail by message ID."""
        try:
            service = await asyncio.to_thread(self._build_service_sync)
            msg = await asyncio.to_thread(
                lambda: service.users()
                .messages()
                .get(userId="me", id=gmail_id, format="full")
                .execute()
            )
            return self._extract_body(msg.get("payload", {}))
        except (EmailServiceUnavailable, RefreshError):
            raise
        except Exception as e:
            logger.error("Error fetching email body %s: %s", gmail_id, e)
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
