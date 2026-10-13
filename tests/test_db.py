import asyncio
from datetime import datetime, timedelta, timezone

from bot.db import DB

UTC = timezone.utc


def test_due_respects_status(tmp_path):
    async def run():
        db = DB(str(tmp_path / "t.sqlite"))
        await db.open()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await db.save_session(1, "***", "enc", now)
        a = await db.account()
        await db.upsert_group(a["id"], -100, "G")
        g = (await db.groups(a["id"]))[0]
        await db.x("UPDATE groups SET ads_allowed=1 WHERE id=?", (g["id"],))
        ad = await db.create_ad(a["id"], None, ["t"], [g["id"]], 240, {g["id"]: now}, now)
        assert len(await db.due(now + timedelta(seconds=1))) == 1
        await db.set_ad_status(ad, "paused")
        assert await db.due(now + timedelta(seconds=1)) == []
        await db.set_ad_status(ad, "active")
        await db.set_account_status(a["id"], "restricted")
        assert await db.due(now + timedelta(seconds=1)) == []
        await db.close()
    asyncio.run(run())
