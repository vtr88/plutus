from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    bot_token: str
    database_path: Path


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing. Copy .env.example to .env and fill it in.")

    database_path = Path(os.getenv("DATABASE_PATH", "data/plutus.sqlite3")).expanduser()
    return Settings(bot_token=bot_token, database_path=database_path)
