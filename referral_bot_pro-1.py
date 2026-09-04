"""
IDEAL REFERAL BOT — ichki ball tizimi + haqiqiy Telegram Stars (gift) integratsiyasi

O'rnatish:
    pip install aiogram aiosqlite python-dotenv

.env fayl yarating (bot bilan bir papkada):
    BOT_TOKEN=1234567890:ABCDEF...
    BOT_USERNAME=your_bot_username
    ADMIN_IDS=123456789,987654321
    REWARD_STARS=10
    GIFT_THRESHOLD=100
    GIFT_ID=5170233102089322756      # sendGift uchun kerakli gift_id (ixtiyoriy)
    REQUIRED_CHANNEL=@your_channel   # ixtiyoriy: obuna majburiyati

Ishga tushirish:
    python referral_bot_pro.py

XUSUSIYATLAR:
    - Har bir userga noyob referal havola
    - Anti-fraud: o'zini-o'zi referal qilish, ikki marta hisoblash bloklanadi
    - (Ixtiyoriy) kanalga obuna bo'lmaguncha referal hisoblanmaydi
    - GIFT_THRESHOLD ballga yetganda bot avtomatik haqiqiy Stars gift yuboradi
      (buning uchun bot balansida yetarli Stars bo'lishi shart)
    - /balance, /referal, /top, /stats (admin), /addstars (admin)
"""

import asyncio
import logging
import os
from contextlib import suppress

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()

# ----------------- SOZLAMALAR -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot_username")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
REWARD_STARS = int(os.getenv("REWARD_STARS", "10"))
GIFT_THRESHOLD = int(os.getenv("GIFT_THRESHOLD", "100"))
GIFT_ID = os.getenv("GIFT_ID", "")  # sendGift uchun; bo'sh bo'lsa gift o'chirilgan
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "")  # bo'sh bo'lsa tekshirilmaydi
DB_PATH = "referral_bot.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("referral_bot")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ----------------- DATABASE -----------------
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                stars INTEGER DEFAULT 0,
                referrer_id INTEGER,
                referrals_count INTEGER DEFAULT 0,
                gift_sent INTEGER DEFAULT 0
            )
            """
        )
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


async def create_user(user_id: int, username: str, referrer_id: int | None) -> bool:
    if await get_user(user_id):
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)",
            (user_id, username, referrer_id),
        )
        await db.commit()
    return True


async def add_stars(user_id: int, amount: int) -> int:
    """Ball qo'shadi va yangilangan balansni qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET stars = stars + ?, referrals_count = referrals_count + 1 WHERE user_id = ?",
            (amount, user_id),
        )
        await db.commit()
        cur = await db.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0


async def mark_gift_sent(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET gift_sent = 1 WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_top_users(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT username, stars, referrals_count FROM users ORDER BY stars DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*), COALESCE(SUM(stars),0) FROM users")
        return await cur.fetchone()


# ----------------- YORDAMCHI FUNKSIYALAR -----------------
async def is_subscribed(user_id: int) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    with suppress(Exception):
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    return False


async def try_send_gift(user_id: int, stars_balance: int, user_row) -> None:
    """GIFT_THRESHOLD ga yetganda va hali gift yuborilmagan bo'lsa, haqiqiy Stars gift yuboradi."""
    if not GIFT_ID or stars_balance < GIFT_THRESHOLD or user_row[5] == 1:
        return
    try:
        await bot.send_gift(chat_id=user_id, gift_id=GIFT_ID)
        await mark_gift_sent(user_id)
        await bot.send_message(
            user_id,
            f"🎁 Tabriklaymiz! Siz {GIFT_THRESHOLD} ⭐ ballga yetdingiz va sizga "
            f"haqiqiy Telegram sovg'asi yuborildi!",
        )
    except Exception as e:
        log.warning(f"Gift yuborishda xatolik (user {user_id}): {e}")


# ----------------- HANDLERLAR -----------------
@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    args = message.text.split(maxsplit=1)
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        candidate = int(args[1])
        if candidate != user_id:
            referrer_id = candidate

    is_new = await create_user(user_id, username, referrer_id)

    if is_new and referrer_id:
        referrer = await get_user(referrer_id)
        if referrer and await is_subscribed(user_id):
            new_balance = await add_stars(referrer_id, REWARD_STARS)
            updated_referrer = await get_user(referrer_id)
            with suppress(Exception):
                await bot.send_message(
                    referrer_id,
                    f"🎉 Yangi referal qo'shildi!\n"
                    f"+{REWARD_STARS} ⭐ | Jami balans: <b>{new_balance}</b> ⭐",
                )
            await try_send_gift(referrer_id, new_balance, updated_referrer)
        elif referrer and REQUIRED_CHANNEL:
            with suppress(Exception):
                await bot.send_message(
                    referrer_id,
                    "⚠️ Sizning referalingiz kanalga obuna bo'lmagani uchun hozircha hisoblanmadi.",
                )

    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    extra = f"\n\n📢 Botdan foydalanish uchun avval {REQUIRED_CHANNEL} kanaliga obuna bo'ling." if REQUIRED_CHANNEL else ""
    await message.answer(
        f"Salom, {message.from_user.full_name}! 👋\n\n"
        f"Do'stlaringizni taklif qiling — har bir referal uchun {REWARD_STARS} ⭐ stars, "
        f"{GIFT_THRESHOLD} ⭐ to'plasangiz haqiqiy Telegram sovg'asi olasiz!\n\n"
        f"🔗 Sizning referal havolangiz:\n<code>{ref_link}</code>{extra}\n\n"
        f"/balance — balansni ko'rish\n/top — reyting"
    )


@dp.message(Command("balance"))
async def balance_handler(message: Message) -> None:
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Iltimos, avval /start buyrug'ini yuboring.")
        return
    await message.answer(
        f"⭐ Balansingiz: <b>{user[2]}</b> stars\n"
        f"👥 Jami referallar: <b>{user[4]}</b> ta\n"
        f"🎁 Gift chegarasi: {GIFT_THRESHOLD} ⭐ ({'olingan ✅' if user[5] else 'hali yo‘q'})"
    )


@dp.message(Command("referal"))
async def referal_handler(message: Message) -> None:
    ref_link = f"https://t.me/{BOT_USERNAME}?start={message.from_user.id}"
    await message.answer(f"🔗 Sizning referal havolangiz:\n<code>{ref_link}</code>")


@dp.message(Command("top"))
async def top_handler(message: Message) -> None:
    top = await get_top_users()
    if not top:
        await message.answer("Hozircha reytingda hech kim yo'q.")
        return
    text = "🏆 <b>Top foydalanuvchilar:</b>\n\n"
    for i, (username, stars, referrals) in enumerate(top, start=1):
        text += f"{i}. {username or 'Foydalanuvchi'} — {stars} ⭐ ({referrals} referal)\n"
    await message.answer(text)


@dp.message(Command("stats"))
async def stats_handler(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    total_users, total_stars = await get_stats()
    await message.answer(f"📊 Jami foydalanuvchilar: {total_users}\n⭐ Jami taqsimlangan stars: {total_stars}")


@dp.message(Command("addstars"))
async def addstars_handler(message: Message) -> None:
    """Admin: /addstars <user_id> <miqdor>"""
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await message.answer("Foydalanish: /addstars <user_id> <miqdor>")
        return
    target_id, amount = int(parts[1]), int(parts[2])
    if not await get_user(target_id):
        await message.answer("Bunday foydalanuvchi topilmadi.")
        return
    new_balance = await add_stars(target_id, amount)
    await message.answer(f"✅ {target_id} ga {amount} ⭐ qo'shildi. Yangi balans: {new_balance}")


# ----------------- ISHGA TUSHIRISH -----------------
async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi! .env faylga qo'shing.")
    await init_db()
    log.info("Bot ishga tushdi.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
