"""Сохранить зашифрованную сессию в БД на сервере.

  echo "<зашифрованная строка>" | python scripts/import_session.py "+996***12"
"""
import asyncio
import sys

from bot.config import load_config
from bot.db import DB
from bot.limits import utcnow


async def main():
    cfg = load_config()
    enc = sys.stdin.read().strip()
    if not enc:
        sys.exit("пустая сессия")
    db = DB(cfg.db_path)
    await db.open()
    await db.save_session(cfg.owner_id, sys.argv[1] if len(sys.argv) > 1 else "***", enc, utcnow())
    await db.close()
    print("Сессия сохранена. Перезапустите бота.")


asyncio.run(main())
