from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"


@dataclass
class Settings:
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: set[int] = field(default_factory=set)
    llm_provider: str = "cohere"
    llm_model: str = "command-r"
    groq_api_key: str = ""
    cohere_api_key: str = ""
    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"
    db_path: str = ""
    persona: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)
    timezone: str = "America/Los_Angeles"
    log_level: str = "INFO"
    log_dir: str = ""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        logger.warning("Config file not found: %s", path)
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_secrets_from_ssm() -> dict[str, str]:
    try:
        import boto3

        ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "us-west-2"))
        params = ssm.get_parameters_by_path(
            Path="/openear/",
            Recursive=True,
            WithDecryption=True,
        )
        result = {}
        for p in params.get("Parameters", []):
            key = p["Name"].replace("/openear/", "").replace("/", "_")
            result[key] = p["Value"]
        return result
    except Exception as e:
        logger.info("SSM unavailable (using .env fallback): %s", e)
        return {}


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    ssm_secrets = _load_secrets_from_ssm()

    persona = _load_yaml(CONFIG_DIR / "persona.yaml")
    rules = _load_yaml(CONFIG_DIR / "rules.yaml")

    raw_ids = (
        ssm_secrets.get("telegram_allowed_user_ids", "")
        or os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    )
    allowed_ids: set[int] = set()
    for uid in raw_ids.split(","):
        uid = uid.strip()
        if uid.isdigit():
            allowed_ids.add(int(uid))

    settings = Settings(
        telegram_bot_token=(
            ssm_secrets.get("telegram_bot_token", "")
            or os.getenv("TELEGRAM_BOT_TOKEN", "")
        ),
        telegram_allowed_user_ids=allowed_ids,
        llm_provider=os.getenv("LLM_PROVIDER", "cohere"),
        llm_model=os.getenv("LLM_MODEL", "command-r-08-2024"),
        groq_api_key=(
            ssm_secrets.get("groq_api_key", "")
            or os.getenv("GROQ_API_KEY", "")
        ),
        cohere_api_key=(
            ssm_secrets.get("cohere_api_key", "")
            or os.getenv("COHERE_API_KEY", "")
        ),
        gmail_credentials_path=os.getenv(
            "GMAIL_CREDENTIALS_PATH", "credentials.json"
        ),
        gmail_token_path=os.getenv("GMAIL_TOKEN_PATH", "token.json"),
        db_path=str(DATA_DIR / "openear.db"),
        persona=persona,
        rules=rules,
        timezone=rules.get("email", {}).get("timezone", "America/Los_Angeles"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_dir=os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs")),
    )

    return settings
