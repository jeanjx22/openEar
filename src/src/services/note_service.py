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
from datetime import datetime

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

    def search_by_tag(
        self, tag: str, since: datetime | None = None
    ) -> list[Note]:
        """Search notes by tag, optionally filtered to a time range.

        Args:
            tag: Tag to search for (case-insensitive).
            since: If provided, only return notes created after this datetime.
        """
        with get_session() as session:
            # SQLite JSON: tags column contains JSON array as text
            # Use LIKE as a simple filter, then verify in Python
            stmt = select(Note).where(Note.tags.ilike(f'%"{tag}"%'))
            if since:
                stmt = stmt.where(Note.created_at >= since)
            stmt = stmt.order_by(Note.created_at.desc())
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
