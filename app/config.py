import os
import time

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

# KLAS returns every date as Korean local time and the parsing in
# app/services/kw.py compares those against a naive datetime.now(), so the
# process must run on Korean time or every deadline is computed hours off.
#
# This overwrites TZ rather than defaulting it: a host that sets TZ=UTC itself
# would otherwise silently reintroduce the nine-hour error, and Seoul time is a
# property of the data source here, not a user preference. The escape hatch is
# KW_TZ, which is ours alone and cannot be set by the platform by accident.
os.environ["TZ"] = os.getenv("KW_TZ", "Asia/Seoul")
if hasattr(time, "tzset"):
    time.tzset()

# Writable directory for the SQLite file. On Railway the container filesystem is
# wiped on every deploy, so this must point at a mounted volume (e.g. /data).
DATA_DIR = os.getenv("DATA_DIR", os.getcwd())


def _default_database_url() -> str:
    return f"sqlite+aiosqlite:///{os.path.join(DATA_DIR, 'bot_users.db')}"


def _required_admin_id() -> int:
    raw = os.getenv("ADMIN_ID")
    if not raw or not raw.strip().lstrip("-").isdigit():
        raise RuntimeError(
            "ADMIN_ID must be set to your numeric Telegram user id "
            "(ask @userinfobot for it). Got: %r" % raw
        )
    return int(raw)


def _required_bot_token() -> str:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN must be set (get one from @BotFather).")
    return token


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BOT_TOKEN: str = _required_bot_token()
    # Optional: without it the AI chat replies with an error, but the bot still
    # starts and every KLAS/library/news feature keeps working.
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL") or _default_database_url()
    NOTIFICATION_CHECK_INTERVAL: int = 1800  # 30 minutes
    ADMIN_ID: int = _required_admin_id()
    # Fernet key as a string. Preferred over encryption_key.key on hosts with an
    # ephemeral filesystem: losing the key makes every stored password
    # permanently undecryptable. Falls back to the file when unset.
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")
    DATA_DIR: str = DATA_DIR


settings = Settings()
