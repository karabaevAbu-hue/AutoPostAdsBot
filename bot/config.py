import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    executor_id: int | None
    api_id: int
    api_hash: str
    fernet_key: str
    db_path: str
    photo_dir: str
    tz_offset_hours: int


def load_config() -> Config:
    executor = os.getenv("EXECUTOR_ID", "").strip()
    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        owner_id=int(os.environ["OWNER_ID"]),
        executor_id=int(executor) if executor else None,
        api_id=int(os.environ["API_ID"]),
        api_hash=os.environ["API_HASH"],
        fernet_key=os.environ["FERNET_KEY"],
        db_path=os.getenv("DB_PATH", "data/bot.sqlite"),
        photo_dir=os.getenv("PHOTO_DIR", "data/photos"),
        tz_offset_hours=int(os.getenv("TZ_OFFSET_HOURS", "6")),
    )
