#!/usr/bin/env python3
"""Seed whitelist and recurring reminders into a fresh DB."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.config import load_settings
from src.db.database import init_db, get_session
from src.db.models import SenderWhitelist
from src.services.reminder_service import ReminderService

settings = load_settings()
init_db(settings.db_path)

# Whitelist
senders = [
    ("*@parentsquare.com", "Aaron School (ParentSquare)"),
    ("*via Aeries Communications*", "School District (Aeries)"),
    ("*jennifer flores via aeries communications*", "Aaron Teacher (URGENT)"),
    ("info@bhcskids.com", "Aaron Afterschool (BHC)"),
    ("*@thethinkacademy.com", "Think Academy"),
    ("*think academy*", "Think Academy"),
    ("jeanjx22@gmail.com", "Think Academy (self)"),
    ("*@stanfordchildrens.org", "Stanford Children Hospital"),
    ("*@sutterhealth.org", "Sutter Health"),
    ("*@ucsf.edu", "UCSF"),
    ("*stanford*health*", "Stanford Health"),
    ("*sutter*", "Sutter Health"),
]

with get_session() as s:
    for pattern, label in senders:
        existing = s.query(SenderWhitelist).filter_by(pattern=pattern).first()
        if not existing:
            s.add(SenderWhitelist(pattern=pattern, label=label))
            print(f"  Added: {pattern} -> {label}")
        else:
            print(f"  Exists: {pattern}")

print(f"\n{len(senders)} whitelist entries configured")

# Recurring reminders
svc = ReminderService(settings)
pdt = ZoneInfo("America/Los_Angeles")
now_local = datetime.now(timezone.utc).astimezone(pdt)

for day_name, weekday_num in [("Sunday", 6), ("Tuesday", 1), ("Thursday", 3)]:
    days_ahead = (weekday_num - now_local.weekday()) % 7
    if days_ahead == 0 and now_local.hour >= 20:
        days_ahead = 7
    due = (now_local + timedelta(days=days_ahead)).replace(hour=20, minute=0, second=0, microsecond=0)
    due_utc = due.astimezone(timezone.utc).replace(tzinfo=None)
    r = svc.create_reminder(
        title=f"Remind Ye: Take food out of fridge ({day_name} night)",
        due_at=due_utc,
        recurrence="weekly",
        chat_id=-1003962733226,
    )
    print(f"  Reminder: {r.title} at {due.strftime('%A %I:%M %p')}")

print("\nSeed complete")
