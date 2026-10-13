import asyncio
import logging
import random
from datetime import timedelta

from telethon import errors

from . import limits as L
from .db import DB, parse

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, db: DB, userbot, notify, tz_offset: int):
        self.db, self.ub, self.notify, self.tz = db, userbot, notify, tz_offset
        self._busy = asyncio.Lock()
        self._stop = False
        self._text_idx: dict[int, int] = {}

    async def run(self):
        while not self._stop:
            try:
                if not self._busy.locked():
                    async with self._busy:
                        await self.tick()
            except Exception:
                log.exception("tick failed")
            await asyncio.sleep(60)

    def stop(self):
        self._stop = True

    async def tick(self):
        now = L.utcnow()
        if L.in_quiet_hours(now, self.tz):
            return
        for row in await self.db.due(now):
            if self._stop or not await self._send_one(row):
                break
            await asyncio.sleep(L.random_pause())

    async def _caps_ok(self, group_id, max_per_day) -> bool:
        now = L.utcnow()
        if await self.db.count_ok_since(now - timedelta(hours=1)) >= L.HOURLY_CAP:
            return False
        if await self.db.count_ok_since(now - timedelta(hours=24)) >= L.DAILY_CAP:
            return False
        if max_per_day and await self.db.count_ok_since(now - timedelta(hours=24), group_id) >= max_per_day:
            return False
        return True

    async def _send_one(self, row) -> bool:
        """False -> прервать цикл (аварийная остановка)."""
        ad_id, gid = row["ad_id"], row["group_id"]
        # повторная проверка статуса непосредственно перед отправкой
        st = await self.db.recheck(ad_id, gid)
        if not st or st["ad_status"] != "active" or not st["enabled"] \
                or not st["ads_allowed"] or st["acc_status"] != "active":
            return True
        now = L.utcnow()
        if L.in_quiet_hours(now, self.tz):
            return False
        if not await self._caps_ok(gid, row["max_per_day"]):
            await self.db.defer(ad_id, gid, now + timedelta(minutes=30))
            return True
        texts = await self.db.ad_texts(ad_id)
        i = self._text_idx.get(ad_id, random.randrange(len(texts)))
        self._text_idx[ad_id] = (i + 1) % len(texts)
        text = texts[i % len(texts)]
        acc = await self.db.account()
        warm = L.in_warmup(parse(acc["created_at"]), now)
        interval = L.effective_interval_min(row["interval_min"], row["max_per_day"], warm)
        try:
            msg = await self.ub.send(row["tg_chat_id"], text, row["photo_path"])
        except errors.FloodWaitError as e:
            await self.db.log(ad_id, gid, "flood", f"FloodWait {e.seconds}", now)
            if e.seconds > 3600:
                await self.db.set_account_status(acc["id"], "paused")
                await self.notify(f"⛔ FloodWait {e.seconds}s — аккаунт на паузе. Проверьте /status.", critical=True)
                return False
            await self.db.defer(ad_id, gid, now + timedelta(seconds=e.seconds + 60))
            return False
        except errors.SlowModeWaitError as e:
            await self.db.log(ad_id, gid, "slowmode", f"SlowMode {e.seconds}", now)
            await self.db.x("UPDATE groups SET slow_mode_sec=? WHERE id=?", (e.seconds, gid))
            await self.db.defer(ad_id, gid, now + timedelta(seconds=max(e.seconds, 60) + 60))
        except (errors.ChatWriteForbiddenError, errors.UserBannedInChannelError,
                errors.ChannelPrivateError) as e:
            await self.db.log(ad_id, gid, "error", type(e).__name__, now)
            await self.db.disable_group(gid, type(e).__name__)
            await self.notify(f"⚠️ Группа «{row['title']}» отключена: {type(e).__name__}")
        except errors.ChatSendMediaForbiddenError as e:
            await self.db.log(ad_id, gid, "error", type(e).__name__, now)
            try:  # в этой версии фиксированно: только текст
                msg = await self.ub.send(row["tg_chat_id"], text, row["photo_path"], text_only=True)
            except Exception as e2:
                return await self._fail(row, now, e2)
            await self._ok(row, msg, now, interval)
        except errors.PeerFloodError as e:
            await self.db.log(ad_id, gid, "peerflood", "PeerFloodError", now)
            await self.db.set_account_status(acc["id"], "restricted")
            await self.notify("🛑 PeerFlood: аккаунт ограничен за спам. ВСЕ рассылки остановлены. "
                              "Проверьте @SpamBot.", critical=True)
            return False
        except (errors.UserDeactivatedBanError, errors.UserDeactivatedError,
                errors.AuthKeyUnregisteredError, errors.SessionRevokedError) as e:
            await self.db.log(ad_id, gid, "ban", type(e).__name__, now)
            await self.db.set_account_status(acc["id"], "banned")
            await self.notify(f"🛑 Аккаунт недоступен ({type(e).__name__}). Рассылки остановлены.", critical=True)
            return False
        except Exception as e:
            return await self._fail(row, now, e)
        else:
            await self._ok(row, msg, now, interval)
        return True

    async def _ok(self, row, msg, now, interval):
        ad_id, gid = row["ad_id"], row["group_id"]
        await self.db.log(ad_id, gid, "ok", None, now)
        await self.db.mark_sent(ad_id, gid, msg.id, now, L.next_run(now, interval))

    async def _fail(self, row, now, e) -> bool:
        ad_id, gid = row["ad_id"], row["group_id"]
        log.warning("send failed: %r", e)
        await self.db.log(ad_id, gid, "error", type(e).__name__, now)
        await self.db.defer(ad_id, gid, now + timedelta(hours=1))
        if await self.db.bump_fail(ad_id, gid) >= L.MAX_CONSECUTIVE_FAILS:
            await self.db.disable_group(gid, type(e).__name__)
            await self.notify(f"⚠️ Группа «{row['title']}» отключена после 3 ошибок подряд.")
        return True
