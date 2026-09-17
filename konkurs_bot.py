import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ChatJoinRequest,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
import aiosqlite

# ============================================================
#                       SOZLAMALAR
# ============================================================
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DB_PATH = "database.db"
POINTS_PER_REF = int(os.getenv("POINTS_PER_REF", "1"))  # bitta referal uchun necha ball

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN environment variable topilmadi! Railway'da Variables bo'limiga qo'shing.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ============================================================
#                       DATABASE
# ============================================================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                referrer_id INTEGER,
                points INTEGER DEFAULT 0,
                referrals_count INTEGER DEFAULT 0,
                joined_at TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS channels (
                chat_id TEXT PRIMARY KEY,
                title TEXT,
                type TEXT DEFAULT 'open',
                numeric_id INTEGER,
                invite_link TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS join_requests (
                numeric_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (numeric_id, user_id)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('rate', '1')")
        await db.commit()


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


async def get_user_by_username(username: str):
    username = username.lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        return await cur.fetchone()


async def create_user(user_id: int, username: str, full_name: str, referrer_id: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, full_name, referrer_id, points, referrals_count, joined_at) "
            "VALUES (?, ?, ?, ?, 0, 0, ?)",
            (user_id, username, full_name, referrer_id, datetime.utcnow().isoformat()),
        )
        await db.commit()


async def update_user_profile(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
            (username, full_name, user_id),
        )
        await db.commit()


async def add_points(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def add_referral(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET referrals_count = referrals_count + 1, points = points + ? WHERE user_id = ?",
            (POINTS_PER_REF, user_id),
        )
        await db.commit()


async def get_top(limit: int = 15):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, username, full_name, referrals_count FROM users "
            "ORDER BY referrals_count DESC, points DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def reset_all_referrals():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET referrals_count = 0")
        await db.commit()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*), COALESCE(SUM(points),0), COALESCE(SUM(referrals_count),0) FROM users")
        return await cur.fetchone()


async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def get_channels():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT chat_id, title, type, numeric_id, invite_link FROM channels")
        return await cur.fetchall()


async def add_channel(chat_id: str, title: str, ch_type: str, numeric_id: int, invite_link: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (chat_id, title, type, numeric_id, invite_link) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title, type = excluded.type, "
            "numeric_id = excluded.numeric_id, invite_link = excluded.invite_link",
            (chat_id, title, ch_type, numeric_id, invite_link),
        )
        await db.commit()


async def remove_channel(chat_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM channels WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def save_join_request(numeric_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO join_requests (numeric_id, user_id) VALUES (?, ?)",
            (numeric_id, user_id),
        )
        await db.commit()


async def has_join_request(numeric_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM join_requests WHERE numeric_id = ? AND user_id = ?",
            (numeric_id, user_id),
        )
        return (await cur.fetchone()) is not None


# ============================================================
#                       FSM HOLATLARI
# ============================================================
class Reg(StatesGroup):
    pending_referrer = State()


class AdminStates(StatesGroup):
    waiting_channel = State()
    waiting_channel_type = State()
    waiting_user_target = State()
    waiting_points_add = State()
    waiting_points_sub = State()
    waiting_rate = State()
    waiting_broadcast = State()


# ============================================================
#                       KLAVIATURALAR
# ============================================================
def main_menu_kb(user_id: int) -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="🔗 Referal havolam"), KeyboardButton(text="💰 Ballarim")],
        [KeyboardButton(text="🏆 TOP 15")],
    ]
    if is_admin(user_id):
        kb.append([KeyboardButton(text="⚙️ Admin panel")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📢 Majburiy obunalar", callback_data="adm_channels")
    b.button(text="👥 Foydalanuvchi bilan ishlash", callback_data="adm_user")
    b.button(text="💱 Valyuta kursi", callback_data="adm_rate")
    b.button(text="🔄 Referallarni nollash", callback_data="adm_reset")
    b.button(text="📣 Xabar yuborish (barchaga)", callback_data="adm_broadcast")
    b.button(text="📊 Statistika", callback_data="adm_stats")
    b.adjust(1)
    return b.as_markup()


async def channels_kb() -> InlineKeyboardMarkup:
    chans = await get_channels()
    b = InlineKeyboardBuilder()
    for chat_id, title, ch_type, numeric_id, invite_link in chans:
        icon = "🔒" if ch_type == "request" else "🔓"
        b.button(text=f"❌ {icon} {title}", callback_data=f"adm_delch:{chat_id}")
    b.button(text="➕ Kanal qo'shish", callback_data="adm_addch")
    b.button(text="⬅️ Orqaga", callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


async def subscribe_kb() -> InlineKeyboardMarkup:
    chans = await get_channels()
    b = InlineKeyboardBuilder()
    for chat_id, title, ch_type, numeric_id, invite_link in chans:
        icon = "🔒" if ch_type == "request" else "🔓"
        url = invite_link or (f"https://t.me/{chat_id.lstrip('@')}" if chat_id.startswith("@") else None)
        if url:
            b.button(text=f"{icon} {title}", url=url)
    b.button(text="✅ A'zo bo'ldim / So'rov yubordim", callback_data="check_sub")
    b.adjust(1)
    return b.as_markup()


# ============================================================
#                 MAJBURIY OBUNANI TEKSHIRISH
# ============================================================
async def check_subscription(user_id: int) -> bool:
    chans = await get_channels()
    if not chans:
        return True
    for chat_id, _title, ch_type, numeric_id, _invite_link in chans:
        subscribed = False
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                subscribed = True
        except TelegramBadRequest:
            # bot kanalda admin bo'lmasa yoki a'zolikni tekshira olmasa
            pass

        if not subscribed and ch_type == "request" and numeric_id:
            # "so'rov" (zayavka) turidagi kanal: foydalanuvchi qo'shilish uchun
            # so'rov yuborgan bo'lsa, admin tasdiqlashini kutmasdan yetarli deb hisoblaymiz
            subscribed = await has_join_request(numeric_id, user_id)

        if not subscribed:
            return False
    return True


# ============================================================
#                       FOYDALANUVCHI QISMI
# ============================================================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    args = message.text.split(maxsplit=1)
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        rid = int(args[1])
        if rid != user_id:
            referrer_id = rid

    existing = await get_user(user_id)
    if existing:
        await update_user_profile(user_id, username, full_name)
    else:
        await state.update_data(referrer_id=referrer_id)

    if not await check_subscription(user_id):
        text = (
            "👋 Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling, "
            "so'ng <b>✅ A'zo bo'ldim</b> tugmasini bosing:"
        )
        await message.answer(text, reply_markup=await subscribe_kb())
        return

    await finish_registration(message.chat.id, user_id, username, full_name, state)


async def finish_registration(chat_id: int, user_id: int, username: str, full_name: str, state: FSMContext):
    existing = await get_user(user_id)
    if not existing:
        data = await state.get_data()
        referrer_id = data.get("referrer_id")
        await create_user(user_id, username, full_name, referrer_id)
        if referrer_id:
            ref = await get_user(referrer_id)
            if ref:
                await add_referral(referrer_id)
                try:
                    await bot.send_message(
                        referrer_id,
                        f"🎉 Sizning havolangiz orqali yangi foydalanuvchi qo'shildi!\n"
                        f"+{POINTS_PER_REF} ball qo'shildi.",
                    )
                except Exception:
                    pass
    await state.clear()
    await bot.send_message(
        chat_id,
        "✅ Xush kelibsiz! Quyidagi menyudan foydalaning:",
        reply_markup=main_menu_kb(user_id),
    )


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if await check_subscription(user_id):
        await call.message.delete()
        await finish_registration(
            call.message.chat.id,
            user_id,
            call.from_user.username or "",
            call.from_user.full_name or "",
            state,
        )
    else:
        await call.answer("❌ Siz hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)


@router.message(F.text == "🔗 Referal havolam")
async def my_ref_link(message: Message):
    bot_info = await bot.get_me()
    user = await get_user(message.from_user.id)
    refs = user[5] if user else 0
    link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    await message.answer(
        f"🔗 Sizning referal havolangiz:\n<code>{link}</code>\n\n"
        f"👥 Jalb qilingan referallar: <b>{refs}</b>\n"
        f"🎯 Har bir referal uchun: <b>{POINTS_PER_REF} ball</b>"
    )


@router.message(F.text == "💰 Ballarim")
async def my_points(message: Message):
    user = await get_user(message.from_user.id)
    points = user[4] if user else 0
    rate = await get_setting("rate", "1")
    await message.answer(
        f"💰 Sizning balingiz: <b>{points}</b>\n"
        f"💱 Joriy kurs: 1 ball = {rate}\n\n"
        f"ℹ️ Ballarni valyutaga almashtirish uchun admin bilan bog'laning."
    )


@router.message(F.text == "🏆 TOP 15")
async def top15(message: Message):
    top = await get_top(15)
    if not top:
        await message.answer("Hozircha reyting bo'sh.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>TOP 15 - eng ko'p referal</b>\n"]
    for i, (uid, uname, fname, refs) in enumerate(top, start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        display = f"@{uname}" if uname else (fname or f"ID:{uid}")
        lines.append(f"{prefix} {display} — <b>{refs}</b> ta referal")
    await message.answer("\n".join(lines))


# ============================================================
#                       ADMIN PANEL
# ============================================================
@router.message(F.text == "⚙️ Admin panel")
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⚙️ <b>Admin panel</b>", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_back")
async def adm_back(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text("⚙️ <b>Admin panel</b>", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    total_users, total_points, total_refs = await get_stats()
    await call.message.edit_text(
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: {total_users}\n"
        f"💰 Umumiy ballar: {total_points}\n"
        f"🔗 Umumiy referallar: {total_refs}",
        reply_markup=admin_menu_kb(),
    )


# ---- Majburiy obunalar ----
@router.callback_query(F.data == "adm_channels")
async def adm_channels(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.message.edit_text("📢 <b>Majburiy obunalar</b>", reply_markup=await channels_kb())


@router.callback_query(F.data == "adm_addch")
async def adm_addch(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_channel)
    await call.message.edit_text(
        "➕ Kanal username'ini yuboring (masalan: <code>@mychannel</code>).\n"
        "Bot kanalda <b>admin</b> bo'lishi shart."
    )


@router.message(AdminStates.waiting_channel)
async def adm_addch_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    chat_id = message.text.strip()
    try:
        chat = await bot.get_chat(chat_id)
    except Exception as e:
        await message.answer(f"❌ Xatolik: kanal topilmadi yoki bot admin emas.\n{e}")
        return
    await state.update_data(new_chat_id=chat_id, new_chat_title=chat.title or chat_id, new_numeric_id=chat.id)
    await state.set_state(AdminStates.waiting_channel_type)
    b = InlineKeyboardBuilder()
    b.button(text="🔓 Ochiq kanal (oddiy a'zolik)", callback_data="adm_chtype:open")
    b.button(text="🔒 Yopiq kanal (so'rov/zayavka orqali)", callback_data="adm_chtype:request")
    b.adjust(1)
    await message.answer(
        f"📌 Kanal: <b>{chat.title}</b>\n\nBu kanal qanday turda ishlaydi?",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("adm_chtype:"))
async def adm_chtype(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    ch_type = call.data.split(":", 1)[1]
    data = await state.get_data()
    chat_id = data.get("new_chat_id")
    title = data.get("new_chat_title")
    numeric_id = data.get("new_numeric_id")

    invite_link = None
    if chat_id.startswith("@"):
        invite_link = f"https://t.me/{chat_id.lstrip('@')}"
    else:
        try:
            if ch_type == "request":
                link = await bot.create_chat_invite_link(chat_id, creates_join_request=True)
                invite_link = link.invite_link
            else:
                invite_link = await bot.export_chat_invite_link(chat_id)
        except Exception:
            invite_link = None

    await add_channel(chat_id, title, ch_type, numeric_id, invite_link)
    await state.clear()
    type_label = "🔒 Yopiq (so'rov orqali)" if ch_type == "request" else "🔓 Ochiq"
    await call.message.edit_text(
        f"✅ Kanal qo'shildi: {title}\nTuri: {type_label}",
        reply_markup=await channels_kb(),
    )


@router.callback_query(F.data.startswith("adm_delch:"))
async def adm_delch(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    chat_id = call.data.split(":", 1)[1]
    await remove_channel(chat_id)
    await call.message.edit_text("📢 <b>Majburiy obunalar</b>", reply_markup=await channels_kb())


# ---- Foydalanuvchi bilan ishlash (ball qo'shish/ayirish) ----
@router.callback_query(F.data == "adm_user")
async def adm_user(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_user_target)
    await call.message.edit_text(
        "👥 Foydalanuvchi <b>ID</b> yoki <b>@username</b> ini yuboring:"
    )


@router.message(AdminStates.waiting_user_target)
async def adm_user_target(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    user = await get_user_by_username(text) if text.startswith("@") else (
        await get_user(int(text)) if text.isdigit() else None
    )
    if not user:
        await message.answer("❌ Foydalanuvchi topilmadi. Qaytadan yuboring:")
        return
    uid, uname, fname, ref_id, points, refs, joined = user
    await state.update_data(target_id=uid)
    b = InlineKeyboardBuilder()
    b.button(text="➕ Ball qo'shish", callback_data="adm_addpts")
    b.button(text="➖ Ball ayirish", callback_data="adm_subpts")
    b.button(text="⬅️ Orqaga", callback_data="adm_back")
    b.adjust(1)
    await message.answer(
        f"👤 <b>{fname}</b> (@{uname or '—'})\n"
        f"ID: <code>{uid}</code>\n"
        f"💰 Ball: {points}\n"
        f"🔗 Referallar: {refs}",
        reply_markup=b.as_markup(),
    )
    await state.set_state(None)


@router.callback_query(F.data == "adm_addpts")
async def adm_addpts(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_points_add)
    await call.message.answer("➕ Necha ball qo'shamiz? Sonini yuboring:")


@router.message(AdminStates.waiting_points_add)
async def adm_addpts_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text.strip().lstrip("-").isdigit():
        await message.answer("❌ Faqat son kiriting.")
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    amount = int(message.text.strip())
    await add_points(target_id, amount)
    await message.answer(f"✅ {amount} ball qo'shildi.", reply_markup=admin_menu_kb())
    try:
        await bot.send_message(target_id, f"💰 Sizga admin tomonidan {amount} ball qo'shildi.")
    except Exception:
        pass
    await state.clear()


@router.callback_query(F.data == "adm_subpts")
async def adm_subpts(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_points_sub)
    await call.message.answer("➖ Necha ball ayiramiz? Sonini yuboring:")


@router.message(AdminStates.waiting_points_sub)
async def adm_subpts_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text.strip().isdigit():
        await message.answer("❌ Faqat musbat son kiriting.")
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    amount = int(message.text.strip())
    await add_points(target_id, -amount)
    await message.answer(f"✅ {amount} ball ayirildi.", reply_markup=admin_menu_kb())
    try:
        await bot.send_message(target_id, f"💰 Sizdan admin tomonidan {amount} ball ayirildi.")
    except Exception:
        pass
    await state.clear()


# ---- Valyuta kursi ----
@router.callback_query(F.data == "adm_rate")
async def adm_rate(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    rate = await get_setting("rate", "1")
    await state.set_state(AdminStates.waiting_rate)
    await call.message.edit_text(
        f"💱 Joriy kurs: <b>1 ball = {rate}</b>\n\nYangi qiymatni yuboring (masalan: <code>500 so'm</code>):"
    )


@router.message(AdminStates.waiting_rate)
async def adm_rate_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await set_setting("rate", message.text.strip())
    await message.answer(f"✅ Kurs yangilandi: 1 ball = {message.text.strip()}", reply_markup=admin_menu_kb())
    await state.clear()


# ---- Referallarni nollash ----
@router.callback_query(F.data == "adm_reset")
async def adm_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ha, nollash", callback_data="adm_reset_confirm")
    b.button(text="❌ Bekor qilish", callback_data="adm_back")
    b.adjust(1)
    await call.message.edit_text(
        "⚠️ Barcha foydalanuvchilarning <b>referal sonini</b> nollashni tasdiqlaysizmi?\n"
        "(Ballar o'zgarmaydi, faqat yangi konkurs uchun referal hisoblagichi 0 ga tushadi)",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "adm_reset_confirm")
async def adm_reset_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await reset_all_referrals()
    await call.message.edit_text("✅ Barcha referallar nollandi. Yangi konkurs boshlandi!", reply_markup=admin_menu_kb())


# ---- Xabar yuborish (broadcast) ----
@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await call.message.edit_text("📣 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring:")


@router.message(AdminStates.waiting_broadcast)
async def adm_broadcast_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    ids = await get_all_user_ids()
    sent, failed = 0, 0
    status = await message.answer(f"⏳ Yuborilmoqda... 0/{len(ids)}")
    for i, uid in enumerate(ids, start=1):
        try:
            await message.copy_to(uid)
            sent += 1
        except Exception:
            failed += 1
        if i % 25 == 0:
            try:
                await status.edit_text(f"⏳ Yuborilmoqda... {i}/{len(ids)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Yuborildi: {sent} ta\n❌ Xatolik: {failed} ta")
    await state.clear()


# ============================================================
#              SO'ROV (ZAYAVKA) ORQALI QO'SHILISH
# ============================================================
@router.chat_join_request()
async def on_join_request(request: ChatJoinRequest):
    await save_join_request(request.chat.id, request.from_user.id)


# ============================================================
#                       ISHGA TUSHIRISH
# ============================================================
async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
