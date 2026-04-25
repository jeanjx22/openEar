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
