import os
import uuid
from datetime import timedelta

from aiogram import F, Router, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import limits as L
from .db import DB, parse

PAGE = 8
MAX_VARIANTS = 3


class NewAd(StatesGroup):
    photo = State()
    text = State()
    variants = State()
    groups = State()
    interval = State()
    confirm = State()


def build_router(db: DB, cfg, userbot, scheduler, notify) -> Router:
    r = Router()
    r.message.filter(F.from_user.id == cfg.owner_id)          # whitelist
    r.callback_query.filter(F.from_user.id == cfg.owner_id)

    async def acc():
        return await db.account()

    @r.message(CommandStart())
    async def start(m: Message):
        a = await acc()
        if not a:
            return await m.answer("Аккаунт не подключён. Подключение выполняет Исполнитель.")
        await m.answer(f"Аккаунт {a['phone_masked']} — статус: {a['status']}.\n"
                       "/new — новое объявление\n/ads — мои объявления\n/groups — группы\n"
                       "/status — здоровье аккаунта\n/pause_all — аварийная остановка\n/help")

    @r.message(Command("help"))
    async def help_(m: Message):
        await m.answer(
            "Бот публикует объявления в выбранные вами группы по расписанию.\n"
            f"Правила (отключить нельзя): интервал не меньше {L.MIN_INTERVAL_MIN // 60} ч, "
            f"не более {L.HOURLY_CAP} постов/час и {L.DAILY_CAP}/сутки, тихие часы "
            f"{L.QUIET_START_H}:00–{L.QUIET_END_H}:00, первые {L.WARMUP_DAYS} дней — прогрев.\n"
            "Выбирайте только группы, где реклама разрешена.")

    # ---------- создание объявления
    @r.message(Command("new"))
    async def new(m: Message, state: FSMContext):
        a = await acc()
        if not a or a["status"] != "active":
            return await m.answer("Аккаунт недоступен. Смотрите /status.")
        await state.clear()
        await state.set_state(NewAd.photo)
        await m.answer("Пришлите фото объявления (1 шт.) или /skip, чтобы без фото.")

    @r.message(NewAd.photo, Command("skip"))
    async def skip_photo(m: Message, state: FSMContext):
        await state.update_data(photo=None, texts=[])
        await state.set_state(NewAd.text)
        await m.answer(f"Текст объявления (до {L.CAPTION_LIMIT} символов).")

    @r.message(NewAd.photo, F.photo)
    async def got_photo(m: Message, state: FSMContext, bot: Bot):
        os.makedirs(cfg.photo_dir, exist_ok=True)
        path = os.path.join(cfg.photo_dir, f"{uuid.uuid4().hex}.jpg")
        await bot.download(m.photo[-1], destination=path)
        await state.update_data(photo=path, texts=[])
        await state.set_state(NewAd.text)
        await m.answer(f"Текст объявления (до {L.CAPTION_LIMIT} символов).")

    @r.message(NewAd.text, F.text)
    async def got_text(m: Message, state: FSMContext):
        if not L.caption_ok(m.text):
            return await m.answer(f"Слишком длинно ({len(m.text)}). Сократите до {L.CAPTION_LIMIT}.")
        await state.update_data(texts=[m.text])
        await state.set_state(NewAd.variants)
        await m.answer(f"Можно добавить ещё до {MAX_VARIANTS - 1} вариантов текста (так безопаснее). "
                       "Присылайте по одному, затем /done.")

    @r.message(NewAd.variants, Command("done"))
    async def variants_done(m: Message, state: FSMContext):
        await state.update_data(selected=[], page=0)
        await state.set_state(NewAd.groups)
        await show_groups(m, state, edit=False)

    @r.message(NewAd.variants, F.text)
    async def got_variant(m: Message, state: FSMContext):
        d = await state.get_data()
        if not L.caption_ok(m.text):
            return await m.answer(f"Слишком длинно. Не более {L.CAPTION_LIMIT}.")
        texts = d["texts"] + [m.text]
        await state.update_data(texts=texts)
        if len(texts) >= MAX_VARIANTS:
            return await variants_done(m, state)
        await m.answer(f"Вариантов: {len(texts)}. Ещё один или /done.")

    async def show_groups(target, state: FSMContext, edit: bool):
        a = await acc()
        d = await state.get_data()
        gs = await db.groups(a["id"])
        if not gs:
            gs_live = await userbot.groups()
            for cid, title in gs_live:
                await db.upsert_group(a["id"], cid, title)
            gs = await db.groups(a["id"])
        page = d.get("page", 0)
        sel = set(d.get("selected", []))
        kb = InlineKeyboardBuilder()
        for g in gs[page * PAGE:(page + 1) * PAGE]:
            mark = "✅" if g["id"] in sel else "▫️"
            kb.button(text=f"{mark} {g['title'][:40]}", callback_data=f"g:{g['id']}")
        kb.adjust(1)
        nav = []
        if page > 0:
            nav.append(("⬅️", f"gp:{page - 1}"))
        if (page + 1) * PAGE < len(gs):
            nav.append(("➡️", f"gp:{page + 1}"))
        for t, c in nav:
            kb.button(text=t, callback_data=c)
        kb.button(text="🔄 Обновить список", callback_data="gsync")
        kb.button(text=f"Далее ({len(sel)})", callback_data="gnext")
        text = "Выберите группы (только где реклама разрешена):"
        if edit:
            await target.message.edit_text(text, reply_markup=kb.as_markup())
        else:
            await target.answer(text, reply_markup=kb.as_markup())

    @r.callback_query(NewAd.groups, F.data == "gsync")
    async def gsync(c: CallbackQuery, state: FSMContext):
        a = await acc()
        for cid, title in await userbot.groups():
            await db.upsert_group(a["id"], cid, title)
        await c.answer("Список обновлён")
        await show_groups(c, state, edit=True)

    @r.callback_query(NewAd.groups, F.data.startswith("gp:"))
    async def gpage(c: CallbackQuery, state: FSMContext):
        await state.update_data(page=int(c.data[3:]))
        await show_groups(c, state, edit=True)

    @r.callback_query(NewAd.groups, F.data.startswith("g:"))
    async def gtoggle(c: CallbackQuery, state: FSMContext):
        gid = int(c.data[2:])
        g = await db.one("SELECT * FROM groups WHERE id=?", (gid,))
        d = await state.get_data()
        sel = list(d.get("selected", []))
        if gid in sel:
            sel.remove(gid)
        elif not g["ads_allowed"]:
            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Да, реклама разрешена, правила проверены", callback_data=f"gok:{gid}")
            kb.button(text="Отмена", callback_data="gp:%d" % d.get("page", 0))
            kb.adjust(1)
            return await c.message.edit_text(
                f"«{g['title']}»: подтвердите, что реклама в этой группе разрешена правилами.",
                reply_markup=kb.as_markup())
        else:
            sel.append(gid)
        await state.update_data(selected=sel)
        await show_groups(c, state, edit=True)

    @r.callback_query(NewAd.groups, F.data.startswith("gok:"))
    async def gconfirm(c: CallbackQuery, state: FSMContext):
        gid = int(c.data[4:])
        await db.x("UPDATE groups SET ads_allowed=1, enabled=1 WHERE id=?", (gid,))
        d = await state.get_data()
        await state.update_data(selected=list(d.get("selected", [])) + [gid])
        await show_groups(c, state, edit=True)

    @r.callback_query(NewAd.groups, F.data == "gnext")
    async def gnext(c: CallbackQuery, state: FSMContext):
        d = await state.get_data()
        n = len(d.get("selected", []))
        a = await acc()
        if not n:
            return await c.answer("Выберите хотя бы одну группу", show_alert=True)
        if L.in_warmup(parse(a["created_at"]), L.utcnow()) and n > L.WARMUP_MAX_GROUPS:
            return await c.answer(f"Прогрев ({L.WARMUP_DAYS} дн.): не более {L.WARMUP_MAX_GROUPS} групп",
                                  show_alert=True)
        kb = InlineKeyboardBuilder()
        for h in L.ALLOWED_INTERVALS_H:
            kb.button(text=f"{h} ч", callback_data=f"i:{h}")
        kb.adjust(3)
        await state.set_state(NewAd.interval)
        await c.message.edit_text("Интервал (если правило группы строже — применится оно):",
                                  reply_markup=kb.as_markup())

    @r.callback_query(NewAd.interval, F.data.startswith("i:"))
    async def interval(c: CallbackQuery, state: FSMContext):
        h = int(c.data[2:])
        if h not in L.ALLOWED_INTERVALS_H:
            return await c.answer()
        await state.update_data(interval_min=h * 60)
        d = await state.get_data()
        kb = InlineKeyboardBuilder()
        kb.button(text="▶️ Запустить", callback_data="go")
        kb.button(text="Отмена", callback_data="cancel")
        await state.set_state(NewAd.confirm)
        await c.message.edit_text(
            f"Предпросмотр:\n\n{d['texts'][0]}\n\nВариантов текста: {len(d['texts'])}\n"
            f"Групп: {len(d['selected'])}\nИнтервал: {h} ч", reply_markup=kb.as_markup())

    @r.callback_query(F.data == "cancel")
    async def cancel(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await c.message.edit_text("Отменено.")

    @r.callback_query(NewAd.confirm, F.data == "go")
    async def go(c: CallbackQuery, state: FSMContext):
        d = await state.get_data()
        a = await acc()
        now = L.utcnow()
        first, ordered = {}, list(d["selected"])
        t = now
        for gid in ordered:  # старты разнесены по времени, а не пачкой
            t += timedelta(seconds=L.random_pause())
            first[gid] = t
        ad_id = await db.create_ad(a["id"], d.get("photo"), d["texts"], ordered,
                                   d["interval_min"], first, now)
        await state.clear()
        await c.message.edit_text(f"Объявление #{ad_id} запущено. Управление: /ads")

    # ---------- мои объявления
    def ad_kb(ad):
        kb = InlineKeyboardBuilder()
        if ad["status"] == "active":
            kb.button(text="⏸ Пауза", callback_data=f"ap:{ad['id']}")
        else:
            kb.button(text="▶️ Продолжить", callback_data=f"ar:{ad['id']}")
        kb.button(text="🗑 Удалить", callback_data=f"ad:{ad['id']}")
        kb.adjust(2)
        return kb.as_markup()

    async def ad_card(ad):
        rows = await db.q(
            "SELECT g.title, ag.last_sent_at, ag.next_run_at, g.last_error FROM ad_groups ag"
            " JOIN groups g ON g.id=ag.group_id WHERE ag.ad_id=?", (ad["id"],))
        lines = [f"#{ad['id']} — {ad['status']}, интервал {ad['interval_min'] // 60} ч"]
        for x in rows:
            lines.append(f"• {x['title'][:30]} | посл: {x['last_sent_at'] or '—'} | след: {x['next_run_at']}"
                         + (f" | ⚠️ {x['last_error']}" if x["last_error"] else ""))
        lines.append("(время UTC)")
        return "\n".join(lines)

    @r.message(Command("ads"))
    async def ads(m: Message):
        a = await acc()
        items = await db.ads(a["id"]) if a else []
        if not items:
            return await m.answer("Объявлений нет. /new")
        for ad in items:
            await m.answer(await ad_card(ad), reply_markup=ad_kb(ad))

    async def refresh(c: CallbackQuery, ad_id: int, note: str):
        ad = await db.ad(ad_id)
        if ad["status"] == "deleted":
            return await c.message.edit_text(f"#{ad_id} удалено.")
        await c.message.edit_text(await ad_card(ad), reply_markup=ad_kb(ad))
        await c.answer(note)

    @r.callback_query(F.data.startswith("ap:"))
    async def pause(c: CallbackQuery):
        i = int(c.data[3:])
        await db.set_ad_status(i, "paused")
        await refresh(c, i, "На паузе")

    @r.callback_query(F.data.startswith("ar:"))
    async def resume(c: CallbackQuery):
        i = int(c.data[3:])
        await db.set_ad_status(i, "active")
        await refresh(c, i, "Продолжено")

    @r.callback_query(F.data.startswith("ad:"))
    async def delete(c: CallbackQuery):
        i = int(c.data[3:])
        await db.set_ad_status(i, "deleted", L.utcnow())
        await refresh(c, i, "Удалено")

    # ---------- группы / статус / аварийная остановка
    @r.message(Command("groups"))
    async def groups(m: Message):
        a = await acc()
        gs = [g for g in await db.groups(a["id"]) if g["ads_allowed"]] if a else []
        if not gs:
            return await m.answer("Подтверждённых групп нет. Они появятся при создании объявления.")
        for g in gs:
            kb = InlineKeyboardBuilder()
            kb.button(text="Выкл" if g["enabled"] else "Вкл", callback_data=f"gt:{g['id']}")
            kb.button(text="N в сутки", callback_data=f"gm:{g['id']}")
            await m.answer(f"{g['title']}\nвкл: {bool(g['enabled'])}, правило: "
                           f"{g['max_per_day'] or '—'}/сутки, slow mode: {g['slow_mode_sec']}с"
                           + (f"\n⚠️ {g['last_error']}" if g["last_error"] else ""),
                           reply_markup=kb.as_markup())

    @r.callback_query(F.data.startswith("gt:"))
    async def gtoggle_enabled(c: CallbackQuery):
        gid = int(c.data[3:])
        await db.x("UPDATE groups SET enabled=1-enabled, last_error=NULL WHERE id=?", (gid,))
        await c.answer("Готово")

    @r.callback_query(F.data.startswith("gm:"))
    async def gmax(c: CallbackQuery):
        gid = int(c.data[3:])
        kb = InlineKeyboardBuilder()
        for n in (1, 2, 3):
            kb.button(text=f"{n}/сутки", callback_data=f"gms:{gid}:{n}")
        await c.message.answer("Сколько постов в сутки разрешают правила группы?", reply_markup=kb.as_markup())
        await c.answer()

    @r.callback_query(F.data.startswith("gms:"))
    async def gmax_set(c: CallbackQuery):
        _, gid, n = c.data.split(":")
        await db.x("UPDATE groups SET max_per_day=? WHERE id=?", (int(n), int(gid)))
        await c.message.edit_text(f"Правило группы: {n} в сутки.")

    @r.message(Command("status"))
    async def status(m: Message):
        a = await acc()
        now = L.utcnow()
        h = await db.count_ok_since(now - timedelta(hours=1))
        d = await db.count_ok_since(now - timedelta(hours=24))
        errs = await db.last_errors()
        warm = a and L.in_warmup(parse(a["created_at"]), now)
        text = (f"Аккаунт: {a['status'] if a else 'нет'}{' (прогрев)' if warm else ''}\n"
                f"Отправок за час: {h}/{L.HOURLY_CAP}, за сутки: {d}/{L.DAILY_CAP}\n"
                f"Тихие часы сейчас: {'да' if L.in_quiet_hours(now, cfg.tz_offset_hours) else 'нет'}\n"
                "Последние ошибки:\n" + ("\n".join(f"• {e['sent_at']} {e['error_code']}" for e in errs) or "—"))
        if a and a["status"] in ("restricted", "banned"):
            text += "\n\nПроверьте аккаунт через @SpamBot."
        await m.answer(text)

    @r.message(Command("pause_all"))
    async def pause_all(m: Message):
        a = await acc()
        if a:
            await db.set_account_status(a["id"], "paused")
        await m.answer("Все отправки остановлены. Возобновить: /resume_all")

    @r.message(Command("resume_all"))
    async def resume_all(m: Message):
        a = await acc()
        if a and a["status"] == "paused":
            await db.set_account_status(a["id"], "active")
            return await m.answer("Отправки возобновлены.")
        await m.answer(f"Статус аккаунта: {a['status'] if a else 'нет'} — вручную не снимается.")

    return r
