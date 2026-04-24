# openEar 🐰 — Personal Assistant Design

## Overview

openEar is a personal AI assistant that communicates via Telegram, monitors Gmail for important emails (school, medical), sends scheduled briefings, manages reminders and notes, and handles general conversation and info lookups — all powered by Groq LLM inference.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Messaging | Telegram Bot | Free API, rich inline keyboards, zero rate-limit friction |
| Hosting | AWS EC2 (t3.micro, Ubuntu 24.04) | Always-on for scheduled tasks, free tier eligible |
| Language | Python 3.12 (async) | Best ecosystem for Gmail, Telegram, Groq SDKs |
| Framework | python-telegram-bot v20+, APScheduler, SQLAlchemy | Mature async libraries, in-process scheduling |
| Database | SQLite | Zero ops for single-user, easy backup, Postgres migration path |
| LLM | Groq API (Llama 3 / Mixtral) | Fast inference, free tier generous for personal use |
| Email filter | Hybrid (whitelist + LLM classification) | Catches known senders fast, LLM catches new ones |
| Reminder UX | Inline keyboards + natural language | Quick taps for structured actions, free-form for ad-hoc |
| Web search | DuckDuckGo (free, no API key) | News/context enrichment, lightweight lookups |
| Weather | Open-Meteo (free, no API key) | Reliable, unlimited calls |
| Stocks | yfinance (free, no API key) | Real-time-ish quotes, zero setup |
| Deployment | Docker Compose on EC2 | Reproducible, one-command deploy, volume-persisted DB |
| Source control | GitHub (private repo, jeanjx22) | Version history, deploy pipeline, public launch path |
| Languages | English + Chinese | LLM handles both natively, responds in user's language |
| Email summary language | Same as source email | Preserves fidelity, user can ask for re-summary in other language |

## Architecture

```
┌───────────────────────────────────────────────────────┐
│                    Telegram Bot                        │
│   ┌─────────┬──────────┬────────┬────────┬─────────┐  │
│   │Briefing │ Reminder │ Notes  │  Chat  │  Tools  │  │
│   │Handler  │ Handler  │Handler │Handler │ Router  │  │
│   └────┬────┴────┬─────┴───┬────┴───┬────┴────┬────┘  │
│        ▼         ▼         ▼        ▼         ▼       │
│  ┌───────┐ ┌────────┐ ┌───────┐ ┌──────┐ ┌────────┐  │
│  │ Email │ │Schedule│ │Storage│ │ LLM  │ │  Info   │  │
│  │Service│ │Service │ │ Layer │ │Service│ │Services │  │
│  │(Gmail)│ │(APSch) │ │(SQLite)│ │(Groq)│ │News/Wx/ │  │
│  │       │ │        │ │       │ │      │ │ Stocks  │  │
│  └───────┘ └────────┘ └───────┘ └──────┘ └────────┘  │
└───────────────────────────────────────────────────────┘
```

Single async Python process. No microservices. Telegram bot uses long polling (no inbound ports needed). Scheduler, bot, and services share memory in-process.

## Data Model

### emails
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| gmail_id | TEXT UNIQUE | dedup key |
| sender | TEXT | |
| subject | TEXT | |
| summary | TEXT | LLM-generated |
| is_important | BOOLEAN | |
| received_at | DATETIME | |
| processed_at | DATETIME | |

### sender_whitelist
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| pattern | TEXT UNIQUE | "teacher@school.edu" or "*@school.edu" |
| label | TEXT | "School", "Medical" |
| created_at | DATETIME | |

### reminders
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| title | TEXT | |
| description | TEXT | |
| due_at | DATETIME | |
| recurrence | TEXT NULL | "daily", "weekly", cron expression |
| status | TEXT | active, snoozed, completed |
| source | TEXT | "email_briefing", "user_manual" |
| source_ref | TEXT NULL | gmail_id if from email |
| created_at | DATETIME | |
| snoozed_until | DATETIME NULL | |

### notes
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| content | TEXT | |
| tags | TEXT | JSON array |
| created_at | DATETIME | |

### conversations
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| role | TEXT | "user" or "assistant" |
| content | TEXT | |
| timestamp | DATETIME | |

Pruned to last 20 messages automatically.

### user_config
| Column | Type | Notes |
|--------|------|-------|
| key | TEXT PK | "timezone", "morning_hour", etc. |
| value | TEXT | |

## Configuration Files

### config/persona.yaml
```yaml
name: openEar
emoji: 🐰
tone: warm, concise, no corporate speak
language:
  primary: English
  supported: [English, Chinese]
  behavior: respond in whatever language the user writes in
email_summary_language: source
behavior:
  - Always acknowledge action items clearly
  - Use emojis sparingly, not every message
  - When unsure, ask rather than guess
  - Keep briefings scannable, not walls of text
  - Sign off reminders with 🐰
```

### config/rules.yaml
```yaml
family:
  sons: []
  husband: {}

email:
  check_times: ["07:00", "20:00"]
  timezone: "America/Los_Angeles"

reminders:
  default_snooze: 1h
  quiet_hours: ["22:00", "07:00"]

notes:
  auto_suggest_reminder: true
```

## Core Flows

### Flow 1: Morning/Evening Email Briefing

1. Scheduler triggers at configured times
2. Gmail API fetches unread since last check
3. For each email:
   - Check sender against whitelist → if match, mark important, tag with label
   - If no match → send subject+snippet to Groq for relevance classification
   - If relevant → full summarization via Groq (in source email language)
   - Store in emails table
4. Compose briefing with categorized summaries and extracted action items
5. Send via Telegram with inline keyboards per action item: [Remind Me] [Already Done] [Dismiss]
6. User taps [Remind Me] → bot asks "When?" → LLM parses natural language time → creates reminder

### Flow 2: Reminders

1. APScheduler fires at due_at time
2. Send Telegram message with inline keyboards: [Done] [Snooze 1hr] [Snooze tomorrow] [Repeat weekly]
3. User taps action → update reminder status accordingly
4. If recurrence set → auto-schedule next occurrence after completion
5. Respect quiet hours — defer to next morning if due during sleep

### Flow 3: Notes

1. User sends note via Telegram (e.g., "note husband has tennis every Thursday 7-9pm")
2. LLM classifies intent as "note", extracts content and tags
3. Store in notes table
4. Bot confirms and offers [Set weekly reminder?] if recurring pattern detected

### Flow 4: Info Lookups (Weather, Stocks, News)

1. User asks a question (e.g., "how's AAPL?" or "weather tomorrow?")
2. LLM classifies intent and selects appropriate tool
3. Tool executes: yfinance for stocks, Open-Meteo for weather, DuckDuckGo for news
4. LLM formats response conversationally

### Flow 5: General Conversation

1. User sends any message not matching above intents
2. LLM responds using conversation history (last 20 messages) for context
3. Persona rules applied via system prompt

## Project Structure

```
openEar/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── config/
│   ├── persona.yaml
│   └── rules.yaml
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers.py
│   │   ├── keyboards.py
│   │   └── formatters.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── email_service.py
│   │   ├── llm_service.py
│   │   ├── reminder_service.py
│   │   ├── note_service.py
│   │   └── info_service.py
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── jobs.py
│   └── db/
│       ├── __init__.py
│       ├── models.py
│       └── database.py
├── scripts/
│   ├── setup_gmail.py
│   └── deploy.sh
└── data/
    └── openear.db
```

## Deployment

### EC2 Setup
- Instance: t3.micro (1 vCPU, 1GB RAM), Ubuntu 24.04 LTS
- Security group: SSH (port 22) inbound only
- Docker + Docker Compose installed
- SQLite persisted via Docker volume at /data/openear.db
- Auto-restart via systemd

### Deploy Flow
```bash
ssh -i key.pem ubuntu@<ec2-ip>
cd openear && git pull && docker compose up -d --build
```

### Required API Keys / Credentials
- TELEGRAM_BOT_TOKEN — from @BotFather on Telegram
- GROQ_API_KEY — from console.groq.com
- Gmail OAuth credentials — one-time setup via scripts/setup_gmail.py

## Future Considerations (not in v1)
- WhatsApp as second messaging channel
- Google Calendar integration for schedule-aware briefings
- Voice messages (Telegram supports them, Groq has Whisper)
- Multi-user support (Postgres migration, user isolation)
- GitHub Actions for auto-deploy on push
