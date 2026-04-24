# openEar 🐰

Personal AI assistant that communicates via Telegram, monitors Gmail for important emails, manages reminders and notes, and handles general conversation.

## Features

- **Email Briefings** — Morning/evening Gmail summaries for school and medical emails with action item extraction
- **Smart Reminders** — Natural language reminder creation with inline keyboard management (snooze, done, repeat)
- **Notes** — Freeform note-taking with auto-tagging
- **Info Lookups** — Weather, stock prices, and news search
- **Bilingual** — English and Chinese, responds in your language
- **General Chat** — Powered by Groq LLM (Llama 3 / Mixtral)

## Quick Start

```bash
# 1. Clone
git clone https://github.com/jeanjx22/openEar.git
cd openEar

# 2. Configure
cp .env.example .env
# Edit .env with your API keys

# 3. Run locally
pip install -r requirements.txt
python -m src.main

# 4. Run with Docker
docker compose up -d
```

## API Keys Needed

| Service | Where to get it |
|---------|----------------|
| Telegram Bot Token | Talk to [@BotFather](https://t.me/BotFather) on Telegram |
| Groq API Key | [console.groq.com](https://console.groq.com) |
| Gmail OAuth | Google Cloud Console → APIs & Services → Credentials |

## Project Structure

```
openEar/
├── config/          # Persona and rules (YAML)
├── src/
│   ├── bot/         # Telegram handlers, keyboards, formatters
│   ├── services/    # Email, LLM, reminder, notes, info services
│   ├── scheduler/   # Scheduled job definitions
│   └── db/          # SQLAlchemy models and database setup
├── scripts/         # Gmail OAuth setup, deploy script
└── docs/plans/      # Design documents
```

## Deployment

Designed for AWS EC2 (t3.micro, Ubuntu 24.04) with Docker Compose. See `docs/plans/openear-design-final.md` for full architecture.
