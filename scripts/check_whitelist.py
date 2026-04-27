#!/usr/bin/env python3
"""Check unread emails against whitelist patterns."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.config import load_settings
from src.db.database import init_db
from src.services.llm_service import LLMService
from src.services.email_service import EmailService

settings = load_settings()
init_db(settings.db_path)
llm = LLMService(settings)
svc = EmailService(settings, llm)

async def check():
    emails = await svc.fetch_unread_emails(max_results=10)
    print(f"Found {len(emails)} unread emails:\n")
    for e in emails:
        sender = e["sender"]
        label = svc._match_whitelist(sender)
        status = f"MATCH -> {label}" if label else "NO MATCH"
        print(f"  Sender: {sender}")
        print(f"  Subject: {e['subject']}")
        print(f"  Status: {status}")
        print()

asyncio.run(check())
