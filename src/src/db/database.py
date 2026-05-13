from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def init_db(db_path: str) -> None:
    global _engine, _SessionLocal

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    with _engine.connect() as conn:
        cols = [r[1] for r in conn.execute(
            __import__("sqlalchemy").text("PRAGMA table_info(sender_whitelist)")
        )]
        if "chat_id" not in cols:
            conn.execute(__import__("sqlalchemy").text(
                "ALTER TABLE sender_whitelist ADD COLUMN chat_id INTEGER"
            ))
            conn.commit()
            logger.info("Migrated sender_whitelist: added chat_id column")

    logger.info("Database initialized at %s", db_path)


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
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
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from src.db.models import Email

    from datetime import datetime, timezone
    stmt = sqlite_insert(Email).values(
        gmail_id=email_obj.gmail_id,
        sender=email_obj.sender,
        subject=email_obj.subject,
        summary=email_obj.summary,
        is_important=email_obj.is_important,
        received_at=email_obj.received_at,
        processed_at=email_obj.processed_at or datetime.now(timezone.utc),
    ).on_conflict_do_nothing(index_elements=["gmail_id"])

    result = session.execute(stmt)
    return result.rowcount > 0
