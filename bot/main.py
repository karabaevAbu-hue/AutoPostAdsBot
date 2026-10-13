import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from .config import load_config
from .db import DB
from .handlers import build_router
from .scheduler import Scheduler
from .userbot import Userbot

log = logging.getLogger("autopost")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    db = DB(cfg.db_path)
    await db.open()
    bot = Bot(cfg.bot_token)
    userbot = Userbot(cfg.api_id, cfg.api_hash, cfg.fernet_key)

    async def notify(text: str, critical: bool = False):
        targets = {cfg.owner_id}
        if critical and cfg.executor_id:
            targets.add(cfg.executor_id)
        for t in targets:
            try:
                await bot.send_message(t, text)
            except Exception:
                log.exception("notify failed")

    acc = await db.account()
    if acc and acc["session_encrypted"]:
        try:
            await userbot.start(acc["session_encrypted"])
        except Exception:
            log.exception("userbot start failed")
            await db.set_account_status(acc["id"], "banned")
            await notify("🛑 Сессия аккаунта не работает (отозвана?). Отправки остановлены.", critical=True)
    else:
        log.warning("account not connected: run scripts/import_session.py")

    scheduler = Scheduler(db, userbot, notify, cfg.tz_offset_hours)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_router(db, cfg, userbot, scheduler, notify))
    task = asyncio.create_task(scheduler.run())
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.stop()
        task.cancel()
        await userbot.stop()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
