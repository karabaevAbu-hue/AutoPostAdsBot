import os
from datetime import datetime, timezone

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, owner_tg_id INTEGER NOT NULL, phone_masked TEXT,
  session_encrypted TEXT, status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, tg_chat_id INTEGER NOT NULL,
  title TEXT, ads_allowed INTEGER NOT NULL DEFAULT 0, max_per_day INTEGER,
  slow_mode_sec INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
  last_error TEXT, UNIQUE(account_id, tg_chat_id));
CREATE TABLE IF NOT EXISTS ads (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, photo_path TEXT,
  status TEXT NOT NULL DEFAULT 'active', interval_min INTEGER NOT NULL,
  created_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS ad_texts (
  id INTEGER PRIMARY KEY, ad_id INTEGER NOT NULL, text TEXT NOT NULL, ord INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ad_groups (
  ad_id INTEGER NOT NULL, group_id INTEGER NOT NULL, next_run_at TEXT NOT NULL,
  last_sent_at TEXT, last_message_id INTEGER, fail_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (ad_id, group_id));
CREATE TABLE IF NOT EXISTS send_log (
  id INTEGER PRIMARY KEY, ad_id INTEGER, group_id INTEGER, sent_at TEXT NOT NULL,
  result TEXT NOT NULL, error_code TEXT);
CREATE INDEX IF NOT EXISTS ix_log_time ON send_log(sent_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def open(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def q(self, sql: str, args: tuple = ()):
        cur = await self.conn.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        return rows

    async def one(self, sql: str, args: tuple = ()):
        rows = await self.q(sql, args)
        return rows[0] if rows else None

    async def x(self, sql: str, args: tuple = ()) -> int:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # --- account
    async def account(self):
        return await self.one("SELECT * FROM accounts ORDER BY id LIMIT 1")

    async def save_session(self, owner_id: int, phone_masked: str, enc: str, now: datetime):
        acc = await self.account()
        if acc:
            await self.x("UPDATE accounts SET session_encrypted=?, phone_masked=?, status='active' WHERE id=?",
                         (enc, phone_masked, acc["id"]))
        else:
            await self.x("INSERT INTO accounts(owner_tg_id,phone_masked,session_encrypted,status,created_at)"
                         " VALUES(?,?,?,?,?)", (owner_id, phone_masked, enc, "active", iso(now)))

    async def set_account_status(self, acc_id: int, status: str):
        await self.x("UPDATE accounts SET status=? WHERE id=?", (status, acc_id))

    # --- groups
    async def upsert_group(self, acc_id: int, chat_id: int, title: str):
        await self.x("INSERT INTO groups(account_id,tg_chat_id,title) VALUES(?,?,?)"
                     " ON CONFLICT(account_id,tg_chat_id) DO UPDATE SET title=excluded.title",
                     (acc_id, chat_id, title))

    async def groups(self, acc_id: int):
        return await self.q("SELECT * FROM groups WHERE account_id=? ORDER BY title COLLATE NOCASE", (acc_id,))

    # --- ads
    async def create_ad(self, acc_id: int, photo: str | None, texts: list[str],
                        group_ids: list[int], interval_min: int, first_runs: dict[int, datetime],
                        now: datetime) -> int:
        cur = await self.conn.execute(
            "INSERT INTO ads(account_id,photo_path,status,interval_min,created_at) VALUES(?,?,?,?,?)",
            (acc_id, photo, "active", interval_min, iso(now)))
        ad_id = cur.lastrowid
        for i, t in enumerate(texts):
            await self.conn.execute("INSERT INTO ad_texts(ad_id,text,ord) VALUES(?,?,?)", (ad_id, t, i))
        for g in group_ids:
            await self.conn.execute("INSERT INTO ad_groups(ad_id,group_id,next_run_at) VALUES(?,?,?)",
                                    (ad_id, g, iso(first_runs[g])))
        await self.conn.commit()
        return ad_id

    async def ads(self, acc_id: int):
        return await self.q("SELECT * FROM ads WHERE account_id=? AND status!='deleted' ORDER BY id DESC", (acc_id,))

    async def ad(self, ad_id: int):
        return await self.one("SELECT * FROM ads WHERE id=?", (ad_id,))

    async def ad_texts(self, ad_id: int):
        return [r["text"] for r in await self.q("SELECT text FROM ad_texts WHERE ad_id=? ORDER BY ord", (ad_id,))]

    async def set_ad_status(self, ad_id: int, status: str, now: datetime | None = None):
        if status == "deleted":
            await self.x("UPDATE ads SET status='deleted', deleted_at=? WHERE id=?", (iso(now), ad_id))
        else:
            await self.x("UPDATE ads SET status=? WHERE id=?", (status, ad_id))

    async def due(self, now: datetime):
        return await self.q(
            "SELECT ag.*, a.photo_path, a.interval_min, a.account_id, g.tg_chat_id, g.title,"
            " g.max_per_day, g.slow_mode_sec"
            " FROM ad_groups ag JOIN ads a ON a.id=ag.ad_id JOIN groups g ON g.id=ag.group_id"
            " JOIN accounts ac ON ac.id=a.account_id"
            " WHERE ag.next_run_at<=? AND a.status='active' AND g.enabled=1 AND g.ads_allowed=1"
            " AND ac.status='active' ORDER BY ag.next_run_at", (iso(now),))

    async def recheck(self, ad_id: int, group_id: int):
        return await self.one(
            "SELECT a.status AS ad_status, g.enabled, g.ads_allowed, ac.status AS acc_status"
            " FROM ads a, groups g, accounts ac WHERE a.id=? AND g.id=? AND ac.id=a.account_id",
            (ad_id, group_id))

    # --- log / counters
    async def log(self, ad_id, group_id, result: str, error: str | None, now: datetime):
        await self.x("INSERT INTO send_log(ad_id,group_id,sent_at,result,error_code) VALUES(?,?,?,?,?)",
                     (ad_id, group_id, iso(now), result, error))

    async def count_ok_since(self, since: datetime, group_id: int | None = None) -> int:
        sql = "SELECT COUNT(*) c FROM send_log WHERE result='ok' AND sent_at>=?"
        args: tuple = (iso(since),)
        if group_id is not None:
            sql += " AND group_id=?"
            args += (group_id,)
        return (await self.one(sql, args))["c"]

    async def mark_sent(self, ad_id, group_id, msg_id, now: datetime, nxt: datetime):
        await self.x("UPDATE ad_groups SET last_sent_at=?, last_message_id=?, next_run_at=?, fail_count=0"
                     " WHERE ad_id=? AND group_id=?", (iso(now), msg_id, iso(nxt), ad_id, group_id))

    async def defer(self, ad_id, group_id, nxt: datetime):
        await self.x("UPDATE ad_groups SET next_run_at=? WHERE ad_id=? AND group_id=?",
                     (iso(nxt), ad_id, group_id))

    async def bump_fail(self, ad_id, group_id) -> int:
        await self.x("UPDATE ad_groups SET fail_count=fail_count+1 WHERE ad_id=? AND group_id=?", (ad_id, group_id))
        return (await self.one("SELECT fail_count FROM ad_groups WHERE ad_id=? AND group_id=?",
                               (ad_id, group_id)))["fail_count"]

    async def disable_group(self, group_id: int, err: str):
        await self.x("UPDATE groups SET enabled=0, last_error=? WHERE id=?", (err, group_id))

    async def last_errors(self, limit: int = 5):
        return await self.q("SELECT sent_at, error_code, group_id FROM send_log WHERE result!='ok'"
                            " ORDER BY id DESC LIMIT ?", (limit,))
