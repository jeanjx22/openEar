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
