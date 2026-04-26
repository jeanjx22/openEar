#!/usr/bin/env python3
"""Quick E2E test for Gmail integration."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv; load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from src.config import load_settings
from src.db.database import init_db
from src.services.llm_service import LLMService
from src.services.email_service import EmailService
from src.bot.formatters import format_briefing

async def main():
    settings = load_settings()
    init_db(settings.db_path)
    llm = LLMService(settings)
    email_svc = EmailService(settings, llm)

    print("1. Testing Gmail fetch...")
    try:
        emails = await email_svc.fetch_unread_emails(max_results=5)
        print(f"   Fetched {len(emails)} unread emails")
        for e in emails[:3]:
            print(f"   - {e['sender']}: {e['subject']}")
    except Exception as ex:
        print(f"   FAIL: {type(ex).__name__}: {ex}")
        return

    print("\n2. Testing email processing (whitelist + LLM classification)...")
    try:
        important = await email_svc.process_emails()
        print(f"   {len(important)} important emails found")
    except Exception as ex:
        print(f"   FAIL: {type(ex).__name__}: {ex}")
        return

    print("\n3. Testing briefing format...")
    briefing = format_briefing(important, settings.timezone)
    print(f"   Briefing:\n{briefing}")

    print("\nE2E PASS")

asyncio.run(main())
