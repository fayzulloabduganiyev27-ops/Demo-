"""
KINO BOT — kod orqali kino topish + to'liq admin boshqaruv paneli

O'rnatish:
    pip install aiogram aiosqlite python-dotenv

.env fayl:
    BOT_TOKEN=1234567890:ABCDEF...
    ADMIN_IDS=123456789,987654321
    REQUIRED_CHANNELS=@kanal1,@kanal2      # boshlang'ich, keyin panel orqali o'zgartiriladi
    DB_PATH=kino_bot.db

Ishga tushirish:
    python kino_bot.py

FOYDALANUVCHI:
    Kino kodini yuboradi (masalan: 101) — bot video + kino haqida ma'lumot bilan javob beradi
    📔 Ma'lumot — admin kiritgan matn

ADMIN (faqat ADMIN_IDS ro'yxatidagilarga "🔧 Boshqaruv" tugmasi ko'rinadi):
    📊 Statistika — obunachilar, kinolar soni, bot pingi va h.k.
    📢 Majburiy kanallar — ochiq (@username) va yopiq/maxfiy (https://t.me/+xxx, forward orqali)
        kanallar, "nechta odam qo'shilgach avtomatik olib tashlansin" chegarasi bilan.
        Yopiq kanalga so'rov (zayavka) yuborgan foydalanuvchi ham hisobga olinadi —
        lekin bot so'rovni AVTOMATIK TASDIQLAMAYDI, faqat tekshiradi.
    🎬 Kinolar — kino qo'shish (kod, nomi, tavsif, video fayl) / o'chirish
    📤 Hammaga xabar yuborish — matn, rasm, video, hatto forward xabar ham bo'lishi mumkin
    📔 Ma'lumot — foydalanuvchilarga ko'rinadigan matnni tahrirlash
"""

import asyncio
import logging
import os
import time
from contextlib import suppress

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

# ----------------- SOZLAMALAR -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DEFAULT_CHANNELS = [c.strip() for c in os.getenv("REQUIRED_CHANNELS", "").split(",") if c.strip()]
DB_PATH = os.getenv("DB_PATH", "kino_bot.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("kino_bot")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# Pastki menyu matnlari
BTN_MOVIE_HINT = "🎬 Kino qidirish"
BTN_INFO = "📔 Ma'lumot"
BTN_BOSHQARUV = "🔧 Boshqaruv"

BTN_AP_STATS = "📊 Statistika"
BTN_AP_CHANNELS = "📢 Majburiy kanallar"
BTN_AP_MOVIES = "🎬 Kinolar"
BTN_AP_BROADCAST = "📤 Hammaga xabar yuborish"
BTN_AP_INFO = "📔 Ma'lumot (tahrirlash)"
BTN_AP_BACK = "⬅️ Asosiy menyu"

MAIN_MENU_TEXTS = {BTN_MOVIE_HINT, BTN_INFO, BTN_BOSHQARUV}
ADMIN_PANEL_TEXTS = {
    BTN_AP_STATS, BTN_AP_CHANNELS, BTN_AP_MOVIES,
    BTN_AP_BROADCAST, BTN_AP_INFO, BTN_AP_BACK,
}
ALL_NAV_TEXTS = MAIN_MENU_TEXTS | ADMIN_PANEL_TEXTS


# ----------------- FSM HOLATLARI -----------------
class AdminStates(StatesGroup):
    waiting_new_channel = State()
    waiting_channel_forward = State()
    waiting_channel_limit = State()
    waiting_new_movie_code = State()
    waiting_new_movie_title = State()
    waiting_new_movie_description = State()
    waiting_new_movie_video = State()
    waiting_broadcast_content = State()
    waiting_info_text = State()


async def cancel_if_nav_button(message: Message, state: FSMContext) -> bool:
    if message.text and message.text in ALL_NAV_TEXTS:
        await state.clear()
        return True
    return False


SKIP_WORDS = {"/skip", "o'tkazib yuborish", "otkazib yuborish", "yo'q", "yoq"}


def is_skip(text: str | None) -> bool:
    return bool(text) and text.strip().lower() in SKIP_WORDS


# ----------------- DATABASE -----------------
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                subscribed INTEGER DEFAULT 0
            )
            """
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT UNIQUE, invite_link TEXT, limit_count INTEGER DEFAULT 0)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS join_requests (user_id INTEGER, channel TEXT, PRIMARY KEY (user_id, channel))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS channel_subscribers (channel TEXT, user_id INTEGER, PRIMARY KEY (channel, user_id))"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                title TEXT,
                description TEXT,
                video_file_id TEXT,
                views INTEGER DEFAULT 0
            )
            """
        )
        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        for ch in DEFAULT_CHANNELS:
            await db.execute("INSERT OR IGNORE INTO channels (channel) VALUES (?)", (ch,))
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('searches_total', '0')"
        )
        await db.commit()


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def increment_setting(key: str) -> None:
    current = await get_setting(key, "0")
    await set_setting(key, str(int(current) + 1))


async def create_user(user_id: int, username: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        await db.commit()


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        return [r[0] for r in await cur.fetchall()]


async def get_users_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        return (await cur.fetchone())[0]


# --- Kanallar ---
async def get_channels() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT channel FROM channels ORDER BY id ASC")
        return [r[0] for r in await cur.fetchall()]


async def get_channel_meta(channel: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT invite_link, limit_count FROM channels WHERE channel = ?", (channel,))
        row = await cur.fetchone()
        return row if row else (None, 0)


async def add_channel(channel: str, invite_link: str | None = None, limit_count: int = 0) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO channels (channel, invite_link, limit_count) VALUES (?, ?, ?)",
            (channel, invite_link, limit_count),
        )
        await db.commit()


async def remove_channel(channel: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM channels WHERE channel = ?", (channel,))
        await db.commit()


async def record_join_request(user_id: int, channel: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO join_requests (user_id, channel) VALUES (?, ?)", (user_id, channel))
        await db.commit()


async def has_join_request(user_id: int, channel: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM join_requests WHERE user_id = ? AND channel = ?", (user_id, channel))
        return (await cur.fetchone()) is not None


async def record_channel_subscriber(channel: str, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO channel_subscribers (channel, user_id) VALUES (?, ?)", (channel, user_id)
        )
        await db.commit()


async def get_channel_subscriber_count(channel: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM channel_subscribers WHERE channel = ?", (channel,))
        return (await cur.fetchone())[0]


# --- Kinolar ---
async def get_movies() -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, code, title, views FROM movies ORDER BY id DESC")
        return await cur.fetchall()


async def get_movie_by_code(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, code, title, description, video_file_id, views FROM movies WHERE code = ?", (code,)
        )
        return await cur.fetchone()


async def add_movie(code: str, title: str, description: str, video_file_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO movies (code, title, description, video_file_id) VALUES (?, ?, ?, ?)",
                (code, title, description, video_file_id),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def delete_movie(movie_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
        await db.commit()


async def increment_movie_views(movie_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE movies SET views = views + 1 WHERE id = ?", (movie_id,))
        await db.commit()


async def get_movies_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM movies")
        return (await cur.fetchone())[0]


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ----------------- KLAVIATURALAR -----------------
def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text=BTN_MOVIE_HINT)],
        [KeyboardButton(text=BTN_INFO)],
    ]
    if is_admin(user_id):
        keyboard.append([KeyboardButton(text=BTN_BOSHQARUV)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def admin_panel_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_AP_STATS)],
            [KeyboardButton(text=BTN_AP_CHANNELS), KeyboardButton(text=BTN_AP_MOVIES)],
            [KeyboardButton(text=BTN_AP_BROADCAST)],
            [KeyboardButton(text=BTN_AP_INFO)],
            [KeyboardButton(text=BTN_AP_BACK)],
        ],
        resize_keyboard=True,
    )


def _channel_api_id(channel: str):
    if channel.startswith("@"):
        return channel
    with suppress(ValueError):
        return int(channel)
    return channel


async def channels_keyboard() -> InlineKeyboardMarkup:
    channels = await get_channels()
    buttons = []
    for ch in channels:
        invite_link, _ = await get_channel_meta(ch)
        url = invite_link if invite_link else f"https://t.me/{ch.lstrip('@')}"
        buttons.append([InlineKeyboardButton(text="📢 Kanalga o'tish", url=url)])
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subs")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ----------------- OBUNA TEKSHIRUV -----------------
async def check_all_subscriptions(user_id: int) -> bool:
    channels = await get_channels()
    if not channels:
        return True
    for ch in channels:
        is_member = False
        try:
            member = await bot.get_chat_member(_channel_api_id(ch), user_id)
            is_member = member.status not in ("left", "kicked")
        except Exception:
            is_member = False

        if is_member:
            await record_channel_subscriber(ch, user_id)
            await maybe_auto_remove_channel(ch)
            continue

        if await has_join_request(user_id, ch):
            await record_channel_subscriber(ch, user_id)
            await maybe_auto_remove_channel(ch)
            continue

        return False
    return True


async def maybe_auto_remove_channel(channel: str) -> None:
    invite_link, limit_count = await get_channel_meta(channel)
    if not limit_count or limit_count <= 0:
        return
    count = await get_channel_subscriber_count(channel)
    if count >= limit_count:
        await remove_channel(channel)
        for admin_id in ADMIN_IDS:
            with suppress(Exception):
                await bot.send_message(
                    admin_id,
                    f"ℹ️ <b>{channel}</b> kanaliga {count} kishi qo'shilgani uchun "
                    f"majburiy obuna ro'yxatidan avtomatik olib tashlandi.",
                )


@dp.chat_join_request()
async def track_join_request(request: ChatJoinRequest) -> None:
    """Zayavka (so'rov orqali qo'shilish) yuborilsa, bot AVTOMATIK TASDIQLAMAYDI —
    faqat kim so'rov yuborganini qayd etadi, shu orqali obuna tekshiruvida hisobga olinadi."""
    channels = await get_channels()
    channel_username = f"@{request.chat.username}" if request.chat.username else None
    channel_id = str(request.chat.id)
    matched = channel_username if channel_username in channels else (channel_id if channel_id in channels else None)
    if matched:
        await record_join_request(request.from_user.id, matched)


async def send_subscription_prompt(chat_id: int) -> None:
    await bot.send_message(
        chat_id,
        "❗️ <b>Botdan foydalanish uchun majburiy kanallarga obuna bo'ling.</b>\n\n"
        "Avval barcha kanallarga obuna bo'ling (yopiq kanal bo'lsa — qo'shilish uchun so'rov "
        "yuboring), keyin «✅ Tekshirish» tugmasini bosing.",
        reply_markup=await channels_keyboard(),
    )


async def send_welcome(chat_id: int, user_id: int, full_name: str) -> None:
    await bot.send_message(
        chat_id,
        f"Assalomu alaykum, {full_name}! 👋\n\n"
        f"Kino botimizga xush kelibsiz! 🎬\n\n"
        f"Istalgan kino kodini yuboring — men sizga kino va u haqida ma'lumot yuboraman.",
        reply_markup=main_menu_keyboard(user_id),
    )


# ----------------- ASOSIY HANDLERLAR -----------------
@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name
    await create_user(user_id, username)

    if not await check_all_subscriptions(user_id):
        await send_subscription_prompt(message.chat.id)
        return

    await send_welcome(message.chat.id, user_id, message.from_user.full_name)


@dp.callback_query(F.data == "check_subs")
async def check_subs_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    if not await check_all_subscriptions(user_id):
        await callback.answer("❌ Siz hali barcha kanallarga obuna bo'lmagansiz!", show_alert=True)
        return
    with suppress(Exception):
        await callback.message.edit_text("✅ Barcha majburiy kanallarga obuna bo'lgansiz.")
    await send_welcome(callback.message.chat.id, user_id, callback.from_user.full_name)
    await callback.answer()


@dp.message(F.text == BTN_MOVIE_HINT)
async def movie_hint_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🎬 Kino kodini yuboring (masalan: <code>101</code>):")


@dp.message(F.text == BTN_INFO)
async def info_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    info_text = await get_setting("info_text", "")
    if not info_text:
        await message.answer("📔 Hozircha ma'lumot kiritilmagan.")
        return
    await message.answer(f"📔 <b>Ma'lumot</b>\n\n{info_text}")


# ----------------- KINO KODI QIDIRUVI -----------------
@dp.message(F.text.regexp(r"^[A-Za-z0-9_\-]{1,20}$"))
async def movie_code_handler(message: Message, state: FSMContext) -> None:
    # Admin panel/asosiy menyu tugmalari bilan chalkashmasligi uchun
    if message.text in ALL_NAV_TEXTS:
        return
    current_state = await state.get_state()
    if current_state is not None:
        return  # FSM kutayotgan bo'lsa, bu handler aralashmasin

    if not await check_all_subscriptions(message.from_user.id):
        await send_subscription_prompt(message.chat.id)
        return

    code = message.text.strip()
    await increment_setting("searches_total")
    movie = await get_movie_by_code(code)
    if not movie:
        await message.answer("❌ Bunday kodli kino topilmadi. Kodni tekshirib, qaytadan yuboring.")
        return

    movie_id, movie_code, title, description, video_file_id, views = movie
    await increment_movie_views(movie_id)
    caption = f"🎬 <b>{title}</b>\n\n{description}" if description else f"🎬 <b>{title}</b>"
    with suppress(Exception):
        await bot.send_video(message.chat.id, video=video_file_id, caption=caption)


# ================= ADMIN BOSHQARUV PANELI =================
@dp.message(F.text == BTN_BOSHQARUV)
async def admin_panel_entry(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🔧 <b>Boshqaruv paneli</b>\n\nKerakli bo'limni tanlang:", reply_markup=admin_panel_menu_keyboard())


@dp.message(F.text == BTN_AP_BACK)
async def admin_panel_exit(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🏠 Asosiy menyu:", reply_markup=main_menu_keyboard(message.from_user.id))


# --- Statistika ---
@dp.message(F.text == BTN_AP_STATS)
async def ap_stats(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()

    start_time = time.monotonic()
    with suppress(Exception):
        await bot.get_me()
    ping_ms = round((time.monotonic() - start_time) * 1000)

    total_users = await get_users_count()
    total_movies = await get_movies_count()
    searches_total = await get_setting("searches_total", "0")
    channels = await get_channels()

    await message.answer(
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Jami obunachilar: {total_users}\n"
        f"🎬 Jami kinolar: {total_movies}\n"
        f"🔎 Jami qidiruvlar: {searches_total}\n"
        f"📢 Majburiy kanallar soni: {len(channels)}\n"
        f"🏓 Bot pingi: {ping_ms} ms"
    )


# --- Majburiy kanallar (Stars botdagi bilan bir xil mantiq) ---
async def send_channels_view(chat_id: int) -> None:
    channels = await get_channels()
    buttons = [
        [InlineKeyboardButton(text=f"❌ {ch}", callback_data=f"ap_delch_{i}")]
        for i, ch in enumerate(channels)
    ]
    buttons.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="ap_addch")])
    text = "📢 <b>Majburiy kanallar</b>\n\n"
    if channels:
        lines = []
        for ch in channels:
            invite_link, limit_count = await get_channel_meta(ch)
            count = await get_channel_subscriber_count(ch)
            line = f"{ch} — 👥 {count} kishi qo'shildi"
            if limit_count and limit_count > 0:
                line += f" (chegarasi: {limit_count})"
            if invite_link:
                line += f"\n🔗 {invite_link}"
            lines.append(line)
        text += "\n\n".join(lines)
    else:
        text += "Hozircha kanal qo'shilmagan."
    text += "\n\nO'chirish uchun kanal ustiga bosing."
    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message(F.text == BTN_AP_CHANNELS)
async def ap_channels(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await send_channels_view(message.chat.id)


@dp.callback_query(F.data.startswith("ap_delch_"))
async def ap_delete_channel(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    idx = int(callback.data.replace("ap_delch_", ""))
    channels = await get_channels()
    if 0 <= idx < len(channels):
        await remove_channel(channels[idx])
        await callback.answer("O'chirildi ✅")
    with suppress(Exception):
        await callback.message.delete()
    await send_channels_view(callback.message.chat.id)


@dp.callback_query(F.data == "ap_addch")
async def ap_add_channel_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_new_channel)
    await bot.send_message(
        callback.from_user.id,
        "➕ Yangi kanalni qo'shing. Ikki xil turi bo'lishi mumkin:\n\n"
        "1️⃣ Ochiq kanal — username yuboring: <code>@mening_kanalim</code>\n"
        "2️⃣ Yopiq/maxfiy kanal — havolasini yuboring: <code>https://t.me/+xxxxxxx</code>\n\n"
        "⚠️ Bot shu kanalda admin bo'lishi shart.",
    )
    await callback.answer()


@dp.message(AdminStates.waiting_new_channel)
async def ap_add_channel_save(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    text = message.text.strip()
    if text.startswith("@"):
        await state.update_data(pending_channel_identifier=text, pending_channel_invite_link=None)
        await state.set_state(AdminStates.waiting_channel_limit)
        await message.answer(
            "👥 Nechta odam qo'shilgach, bu kanal majburiy obunadan <b>avtomatik olib tashlansin</b>?\n"
            "(Cheklov kerak bo'lmasa <code>0</code> yozing)"
        )
    elif text.startswith("https://t.me/"):
        await state.update_data(pending_channel_invite_link=text)
        await state.set_state(AdminStates.waiting_channel_forward)
        await message.answer(
            "📨 Endi shu (maxfiy/yopiq) kanaldan istalgan xabarni botga <b>forward</b> qiling.\n"
            "⚠️ Bot shu kanalda admin bo'lishi shart."
        )
    else:
        await message.answer("Iltimos, @username yoki https://t.me/... havola yuboring.")


def _extract_forwarded_chat_id(message: Message) -> int | None:
    if message.forward_from_chat:
        return message.forward_from_chat.id
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None:
            return chat.id
    return None


@dp.message(AdminStates.waiting_channel_forward)
async def ap_channel_forward_received(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    channel_id = _extract_forwarded_chat_id(message)
    if channel_id is None:
        await message.answer("Iltimos, shu kanaldan biror xabarni forward qiling (oddiy matn emas).")
        return
    await state.update_data(pending_channel_identifier=str(channel_id))
    await state.set_state(AdminStates.waiting_channel_limit)
    await message.answer(
        "✅ Kanal aniqlandi.\n\n"
        "👥 Nechta odam qo'shilgach, bu kanal majburiy obunadan <b>avtomatik olib tashlansin</b>?\n"
        "(Cheklov kerak bo'lmasa <code>0</code> yozing)"
    )


@dp.message(AdminStates.waiting_channel_limit)
async def ap_channel_limit_received(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    if not message.text.isdigit():
        await message.answer("Iltimos, faqat raqam yuboring (cheklov kerak bo'lmasa 0).")
        return
    limit_count = int(message.text)
    data = await state.get_data()
    identifier = data.get("pending_channel_identifier")
    invite_link = data.get("pending_channel_invite_link")
    await state.clear()

    if not identifier:
        await message.answer("Xatolik yuz berdi, qaytadan urinib ko'ring.")
        return

    await add_channel(identifier, invite_link, limit_count)
    limit_note = f" ({limit_count} kishidan keyin avtomatik olib tashlanadi)" if limit_count > 0 else ""
    await message.answer(f"✅ Kanal qo'shildi.{limit_note}")
    await send_channels_view(message.chat.id)


# --- Kinolar boshqaruvi ---
async def send_movies_view(chat_id: int) -> None:
    movies = await get_movies()
    buttons = [
        [InlineKeyboardButton(text=f"🗑 [{code}] {title} — {views} ko'rish", callback_data=f"ap_delmovie_{mid}")]
        for mid, code, title, views in movies
    ]
    buttons.append([InlineKeyboardButton(text="➕ Yangi kino qo'shish", callback_data="ap_addmovie")])
    text = "🎬 <b>Kinolar</b>\n\n"
    text += "O'chirish uchun kino ustiga bosing." if movies else "Hozircha kino qo'shilmagan."
    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message(F.text == BTN_AP_MOVIES)
async def ap_movies(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await send_movies_view(message.chat.id)


@dp.callback_query(F.data.startswith("ap_delmovie_"))
async def ap_delete_movie(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    movie_id = int(callback.data.replace("ap_delmovie_", ""))
    await delete_movie(movie_id)
    await callback.answer("O'chirildi ✅")
    with suppress(Exception):
        await callback.message.delete()
    await send_movies_view(callback.message.chat.id)


@dp.callback_query(F.data == "ap_addmovie")
async def ap_add_movie_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_new_movie_code)
    await bot.send_message(callback.from_user.id, "➕ Kino kodini yuboring (masalan: <code>101</code>, faqat harf/raqam):")
    await callback.answer()


@dp.message(AdminStates.waiting_new_movie_code)
async def ap_add_movie_code(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    code = message.text.strip()
    if not code or " " in code:
        await message.answer("Iltimos, bo'shliqsiz kod yuboring (masalan: 101 yoki kino_1).")
        return
    existing = await get_movie_by_code(code)
    if existing:
        await message.answer("❌ Bu kod band. Boshqa kod kiriting.")
        return
    await state.update_data(new_movie_code=code)
    await state.set_state(AdminStates.waiting_new_movie_title)
    await message.answer("🎬 Kino nomini yuboring:")


@dp.message(AdminStates.waiting_new_movie_title)
async def ap_add_movie_title(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    await state.update_data(new_movie_title=message.text.strip())
    await state.set_state(AdminStates.waiting_new_movie_description)
    await message.answer("📝 Kino haqida qisqacha ma'lumot yuboring (janri, yili va h.k., yoki /skip):")


@dp.message(AdminStates.waiting_new_movie_description)
async def ap_add_movie_description(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    description = "" if is_skip(message.text) else message.text.strip()
    await state.update_data(new_movie_description=description)
    await state.set_state(AdminStates.waiting_new_movie_video)
    await message.answer("🎥 Endi kino video faylini yuboring:")


@dp.message(AdminStates.waiting_new_movie_video, F.video)
async def ap_add_movie_video(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("new_movie_code")
    title = data.get("new_movie_title")
    description = data.get("new_movie_description", "")
    video_file_id = message.video.file_id
    await state.clear()

    success = await add_movie(code, title, description, video_file_id)
    if not success:
        await message.answer("❌ Bu kod band bo'lib qoldi, kino saqlanmadi. Qaytadan urinib ko'ring.")
        return
    await message.answer(f"✅ \"{title}\" (kod: {code}) kinolar ro'yxatiga qo'shildi.")
    await send_movies_view(message.chat.id)


@dp.message(AdminStates.waiting_new_movie_video)
async def ap_add_movie_video_invalid(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    await message.answer("Iltimos, video fayl yuboring (matn emas).")


# --- Ma'lumot (tahrirlash) ---
@dp.message(F.text == BTN_AP_INFO)
async def ap_info_prompt(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    current = await get_setting("info_text", "")
    await state.set_state(AdminStates.waiting_info_text)
    if current:
        await message.answer(f"📔 <b>Joriy matn:</b>\n\n{current}\n\n✍️ Yangi matnni yuboring (eskisi almashadi):")
    else:
        await message.answer("📔 Hozircha matn kiritilmagan.\n\n✍️ Matnni yuboring:")


@dp.message(AdminStates.waiting_info_text)
async def ap_info_save(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    await set_setting("info_text", message.text)
    await state.clear()
    await message.answer("✅ Matn saqlandi.")


# --- Hammaga xabar yuborish (matn/rasm/video/forward — istalgan turdagi xabar) ---
@dp.message(F.text == BTN_AP_BROADCAST)
async def ap_broadcast_prompt(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await state.set_state(AdminStates.waiting_broadcast_content)
    await message.answer(
        "📤 Barcha obunachilarga yubormoqchi bo'lgan xabaringizni yuboring.\n"
        "Bu matn, rasm, video, yoki forward qilingan xabar bo'lishi mumkin — "
        "qanday yuborsangiz, aynan shu ko'rinishda hammaga tarqatiladi."
    )


@dp.message(AdminStates.waiting_broadcast_content)
async def ap_broadcast_send(message: Message, state: FSMContext) -> None:
    if await cancel_if_nav_button(message, state):
        return
    user_ids = await get_all_user_ids()
    await state.clear()
    sent, failed = 0, 0
    status_msg = await message.answer(f"📤 Yuborilmoqda... (0/{len(user_ids)})")
    for uid in user_ids:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ Yuborildi: {sent} ta\n❌ Yuborilmadi: {failed} ta")


# ----------------- ISHGA TUSHIRISH -----------------
async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi! .env yoki Railway Variables'ga qo'shing.")
    await init_db()
    log.info("Bot ishga tushdi.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
