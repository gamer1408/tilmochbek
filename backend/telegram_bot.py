import re
import html
import uuid
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple, Set

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select

from backend.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, TEMP_DIR, PUBLIC_BASE_URL, ADMIN_TELEGRAM_IDS,
    PAYMENT_INFO, SUPPORT_CONTACT, FILE_TTL_HOURS,
)
from backend.models import User
from backend.db import get_db_context
from backend.service import execute_dubbing, friendly_error
from backend.billing import click_enabled, create_click_payment
from backend.pipeline.quota import (
    PLANS, QuotaError, get_usage, ensure_can_start, activate_plan, grant_referral_bonus, REFERRAL_BONUS_VIDEOS,
)

logger = logging.getLogger("Tilmochbek-TelegramBot")

bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None
bot_task: Optional[asyncio.Task] = None

YOUTUBE_URL_REGEX = re.compile(
    r"(https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:\S*&)?v=|shorts/|live/)|youtu\.be/)[\w\-]{6,}[^\s]*)",
    re.IGNORECASE,
)
TG_UPLOAD_LIMIT = 49 * 1024 * 1024          # Bot API: files up to 50 MB can be sent
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024        # Bot API: files up to 20 MB can be downloaded
PENDING_TTL = 600

_pending: Dict[str, Tuple[str, int, float]] = {}   # token -> (url, telegram_user_id, created)
_busy_users: Set[int] = set()
_tasks: Set[asyncio.Task] = set()


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def is_public_url(url: str) -> bool:
    return url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url


def main_keyboard() -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="💎 Tariflar", callback_data="show_plans"),
        InlineKeyboardButton(text="ℹ️ Qo'llanma", callback_data="show_help"),
    ]]
    if is_public_url(PUBLIC_BASE_URL):
        rows.insert(0, [InlineKeyboardButton(text="🌐 Veb-sayt", url=PUBLIC_BASE_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def voice_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👨 Erkak ovozi (Sardor)", callback_data=f"v:male:{token}"),
        InlineKeyboardButton(text="👩 Ayol ovozi (Madina)", callback_data=f"v:female:{token}"),
    ]])


async def safe_edit(msg: types.Message, text: str, **kw):
    try:
        await msg.edit_text(text, **kw)
    except Exception as e:  # "message is not modified", flood limits etc. must never break a job
        logger.debug(f"edit_text skipped: {e}")


async def get_or_create_telegram_user(tg_user: types.User, start_arg: Optional[str] = None) -> User:
    """Finds or registers a Telegram user; supports web account linking (link_) and referrals (ref_)."""
    async with get_db_context() as db:
        if start_arg and start_arg.startswith("link_"):
            web_user = (await db.execute(select(User).where(User.telegram_link_code == start_arg))).scalar_one_or_none()
            if web_user:
                web_user.telegram_id = tg_user.id
                web_user.telegram_username = tg_user.username
                web_user.telegram_link_code = None
                await db.commit()
                await db.refresh(web_user)
                return web_user

        user = (await db.execute(select(User).where(User.telegram_id == tg_user.id))).scalar_one_or_none()
        if user:
            return user

        referrer_id = None
        if start_arg and start_arg.startswith("ref_") and start_arg[4:].isdigit():
            referrer_id = int(start_arg[4:])
        full_name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip() or "Telegram Foydalanuvchi"
        new_user = User(email=None, full_name=full_name, telegram_id=tg_user.id,
                        telegram_username=tg_user.username, referred_by=referrer_id)
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        if referrer_id and referrer_id != new_user.id:
            await grant_referral_bonus(db, referrer_id)
        return new_user


# ---------- commands ----------

async def handle_start_command(message: types.Message):
    parts = (message.text or "").split(maxsplit=1)
    user = await get_or_create_telegram_user(message.from_user, parts[1].strip() if len(parts) > 1 else None)
    async with get_db_context() as db:
        usage = await get_usage(db, user)
    text = (
        f"👋 <b>Assalomu alaykum, {esc(user.full_name)}!</b>\n\n"
        "🎬 <b>Tilmochbek</b> — YouTube videolarni sun'iy intellekt yordamida <b>o'zbek tiliga</b> "
        "tabiiy ovozda dublyaj qiladi.\n\n"
        "📌 <b>Qanday ishlaydi?</b>\n"
        "1️⃣ YouTube havolasini yuboring\n"
        "2️⃣ Ovozni tanlang (erkak yoki ayol)\n"
        "3️⃣ Tayyor dublyaj qilingan videoni oling\n\n"
        f"🎁 Qolgan videolar: <b>{usage['videos_remaining']}</b> ({esc(usage['plan_name'])})\n"
        "Buyruqlar: /tarif · /profil · /taklif · /yordam"
    )
    await message.answer(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


def help_text() -> str:
    return (
        "ℹ️ <b>Qo'llanma</b>\n\n"
        "• YouTube havolasini yuboring (youtube.com yoki youtu.be)\n"
        "• Yoki 20 MB gacha video fayl yuboring\n"
        "• Dublyaj odatda 1–3 daqiqa oladi\n"
        f"• Tayyor fayllar serverda {int(FILE_TTL_HOURS)} soat saqlanadi\n\n"
        "/tarif — tariflar va to'lov\n/profil — qolgan videolar\n/taklif — do'st taklif qilib +1 video oling"
        + (f"\n\nYordam: {esc(SUPPORT_CONTACT)}" if SUPPORT_CONTACT else "")
    )


def plans_text() -> str:
    lines = ["💎 <b>Tariflar</b>\n"]
    for p in PLANS.values():
        price = "Bepul" if p["price"] == 0 else f"{p['price']:,}".replace(",", " ") + " so'm"
        old = f" <s>{p['old_price']:,}</s>".replace(",", " ") if p.get("old_price") else ""
        lines.append(f"• <b>{p['name']}</b> — {price}{old}\n   {p['videos']} ta video / {p['days']} kun, "
                     f"har biri {p['max_minutes']} daqiqagacha")
    if click_enabled():
        lines.append("\n💳 Quyidagi tugmalardan birini bosib <b>Click</b> orqali to'lang, tarif avtomatik yoqiladi.")
    else:
        lines.append(f"\n💳 <b>To'lov:</b> {esc(PAYMENT_INFO)}\nTo'lovdan so'ng chekni yuboring, tarif shu zahoti yoqiladi.")
    return "\n".join(lines)


def plans_keyboard() -> Optional[InlineKeyboardMarkup]:
    if not click_enabled():
        return None
    rows = [[InlineKeyboardButton(text=f"💳 {p['name']} — {p['price']:,} so'm".replace(",", " "), callback_data=f"buy:{p['key']}")]
            for p in PLANS.values() if p["price"] > 0]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def handle_buy_callback(query: types.CallbackQuery):
    plan_key = (query.data or "").split(":", 1)[-1]
    if plan_key not in PLANS or plan_key == "free":
        await query.answer()
        return
    user = await get_or_create_telegram_user(query.from_user)
    try:
        async with get_db_context() as db:
            _, url = await create_click_payment(db, user, plan_key)
    except ValueError:
        await query.answer("Click hozircha yoqilmagan. Karta orqali to'lang.", show_alert=True)
        return
    await query.answer()
    await query.message.answer(
        f"💳 <b>{PLANS[plan_key]['name']}</b> — {PLANS[plan_key]['price']:,} so'm".replace(",", " ") +
        "\nTo'lash uchun pastdagi tugmani bosing. To'lov o'tgach tarif avtomatik yoqiladi.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Click orqali to'lash", url=url)]]),
    )


async def handle_help_callback(query: types.CallbackQuery):
    await query.message.answer(help_text(), parse_mode=ParseMode.HTML)
    await query.answer()


async def handle_plans_callback(query: types.CallbackQuery):
    await query.message.answer(plans_text(), parse_mode=ParseMode.HTML, reply_markup=plans_keyboard())
    await query.answer()


async def handle_plans_command(message: types.Message):
    await message.answer(plans_text(), parse_mode=ParseMode.HTML, reply_markup=plans_keyboard())


async def handle_help_command(message: types.Message):
    await message.answer(help_text(), parse_mode=ParseMode.HTML)


async def handle_profile_command(message: types.Message):
    user = await get_or_create_telegram_user(message.from_user)
    async with get_db_context() as db:
        usage = await get_usage(db, user, 1 if user.telegram_id in _busy_users else 0)
    exp = f"\nTarif muddati: <b>{usage['expires_at'][:10]}</b> gacha" if usage["expires_at"] else ""
    await message.answer(
        f"👤 <b>{esc(user.full_name)}</b>\nTarif: <b>{esc(usage['plan_name'])}</b>{exp}\n"
        f"Ishlatilgan: <b>{usage['videos_used']}/{usage['videos_total']}</b>\n"
        f"Qolgan videolar: <b>{usage['videos_remaining']}</b> (shundan bonus: {usage['bonus_videos']})\n"
        f"Eng uzun video: {usage['max_minutes']} daqiqa",
        parse_mode=ParseMode.HTML,
    )


async def handle_referral_command(message: types.Message):
    user = await get_or_create_telegram_user(message.from_user)
    link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=ref_{user.id}"
    await message.answer(
        f"🎁 <b>Do'stingizni taklif qiling</b> — har bir yangi foydalanuvchi uchun +{REFERRAL_BONUS_VIDEOS} bepul video!\n\n"
        f"Sizning havolangiz:\n{link}",
        parse_mode=ParseMode.HTML,
    )


async def handle_id_command(message: types.Message):
    await message.answer(f"Sizning Telegram ID: <code>{message.from_user.id}</code>", parse_mode=ParseMode.HTML)


async def handle_activate_command(message: types.Message):
    """Admin only: /activate <telegram_id> <plan> [days]  (after a manual payment is confirmed)."""
    if message.from_user.id not in ADMIN_TELEGRAM_IDS:
        return
    args = (message.text or "").split()[1:]
    if len(args) < 2 or not args[0].isdigit() or args[1] not in PLANS or args[1] == "free":
        await message.answer("Format: /activate <telegram_id> <weekly|monthly|vip> [kun]")
        return
    days = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
    async with get_db_context() as db:
        user = (await db.execute(select(User).where(User.telegram_id == int(args[0])))).scalar_one_or_none()
        if not user:
            await message.answer("Foydalanuvchi topilmadi (u avval /start bosishi kerak).")
            return
        user = await activate_plan(db, user, args[1], days)
    await message.answer(f"✅ {user.full_name}: {PLANS[args[1]]['name']} {user.plan_expires_at:%Y-%m-%d} gacha yoqildi.")
    try:
        await message.bot.send_message(int(args[0]), f"✅ <b>{PLANS[args[1]]['name']}</b> tarifi yoqildi! "
                                       f"Muddat: {user.plan_expires_at:%Y-%m-%d} gacha.", parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ---------- dubbing flow ----------

async def handle_youtube_message(message: types.Message):
    match = YOUTUBE_URL_REGEX.search(message.text or "")
    if not match:
        return
    user = await get_or_create_telegram_user(message.from_user)
    try:
        async with get_db_context() as db:
            await ensure_can_start(db, user, 1 if message.from_user.id in _busy_users else 0)
    except QuotaError as e:
        await message.answer(f"⚠️ {esc(str(e))}\n\n{plans_text()}", parse_mode=ParseMode.HTML, reply_markup=plans_keyboard())
        return
    if message.from_user.id in _busy_users:
        await message.answer("⏳ Avvalgi videongiz hali tayyorlanmoqda. Tugagach yangisini yuboring.")
        return

    now = time.time()
    for k in [k for k, v in _pending.items() if now - v[2] > PENDING_TTL]:
        _pending.pop(k, None)
    token = uuid.uuid4().hex[:10]
    _pending[token] = (match.group(1), message.from_user.id, now)
    await message.reply("🎙 Qaysi ovozda dublyaj qilamiz?", reply_markup=voice_keyboard(token))


async def handle_voice_choice(query: types.CallbackQuery):
    try:
        _, gender, token = (query.data or "").split(":", 2)
    except ValueError:
        await query.answer()
        return
    entry = _pending.pop(token, None)
    if not entry or entry[1] != query.from_user.id:
        await query.answer("Havola eskirgan. Qayta yuboring.", show_alert=True)
        return
    if query.from_user.id in _busy_users:
        await query.answer("Avvalgi video tayyorlanmoqda.", show_alert=True)
        return
    await query.answer()
    user = await get_or_create_telegram_user(query.from_user)
    status_msg = await query.message.edit_text("⏳ Dublyaj boshlanmoqda...")
    _spawn(process_job(query.message.chat.id, status_msg, entry[0], True, user, gender, query.from_user.id))


async def handle_video_message(message: types.Message):
    user = await get_or_create_telegram_user(message.from_user)
    media = message.video or message.document
    if not media:
        return
    if (media.file_size or 0) > TG_DOWNLOAD_LIMIT:
        await message.reply("⚠️ Telegram botlar 20 MB dan katta fayllarni qabul qila olmaydi. "
                            "Videoni YouTube'ga joylab havolasini yuboring.")
        return
    if message.from_user.id in _busy_users:
        await message.reply("⏳ Avvalgi videongiz hali tayyorlanmoqda.")
        return
    try:
        async with get_db_context() as db:
            await ensure_can_start(db, user)
    except QuotaError as e:
        await message.answer(f"⚠️ {esc(str(e))}\n\n{plans_text()}", parse_mode=ParseMode.HTML, reply_markup=plans_keyboard())
        return

    status_msg = await message.reply("⏳ Fayl yuklab olinmoqda...")
    try:
        info = await message.bot.get_file(media.file_id)
        folder = TEMP_DIR / "telegram_uploads"
        folder.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w.\-]", "_", getattr(media, "file_name", None) or "video.mp4")
        local_path = str(folder / f"{uuid.uuid4().hex}_{safe_name}")
        await message.bot.download_file(info.file_path, local_path)
    except Exception as e:
        logger.warning(f"Telegram upload download failed: {e}")
        await safe_edit(status_msg, "❌ Faylni yuklab bo'lmadi. Qayta urinib ko'ring yoki YouTube havolasini yuboring.")
        return
    _spawn(process_job(message.chat.id, status_msg, local_path, False, user, "male", message.from_user.id))


def _spawn(coro):
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def process_job(chat_id: int, status_msg: types.Message, source: str, is_youtube: bool,
                      user: User, voice_gender: str, tg_id: int):
    _busy_users.add(tg_id)
    job_id = uuid.uuid4().hex
    labels = {"Navbat": "⏳", "Yuklab olish": "🎬 [1/5]", "AI Transkripsiya": "🎙️ [2/5]", "AI Tarjima": "🌐 [3/5]",
              "Ovoz Sintezi": "🗣️ [4/5]", "Miksing": "🎞️ [5/5]"}

    async def progress(step: str, pct: int, message: str):
        await safe_edit(status_msg, f"{labels.get(step, '⏳')} {message}")

    try:
        result = await execute_dubbing(job_id=job_id, source=source, is_youtube=is_youtube,
                                       voice_gender=voice_gender, user=user, progress=progress)
        out_path = Path(result["out_file"])
        title = result["title"]
        caption = f"✅ <b>{esc(title[:200])}</b>\n⏱ {result['duration'] / 60:.1f} daqiqa · 🎙 Tilmochbek"
        if out_path.stat().st_size <= TG_UPLOAD_LIMIT:
            await safe_edit(status_msg, "🚀 Dublyaj tayyor! Video yuborilmoqda...")
            await status_msg.bot.send_video(chat_id=chat_id, video=FSInputFile(str(out_path), filename="tilmochbek.mp4"),
                                            caption=caption, parse_mode=ParseMode.HTML, supports_streaming=True)
            await status_msg.delete()
        elif is_public_url(PUBLIC_BASE_URL):
            link = f"{PUBLIC_BASE_URL}{result['video_url']}"
            await safe_edit(status_msg, f"{caption}\n\n📥 Video katta (50 MB dan oshiq), yuklab oling "
                            f"({int(FILE_TTL_HOURS)} soat ichida):\n{link}", parse_mode=ParseMode.HTML)
        else:
            await safe_edit(status_msg, f"{caption}\n\n⚠️ Video 50 MB dan katta. Uni veb-saytdan yuklab oling.",
                            parse_mode=ParseMode.HTML)
    except Exception as e:
        await safe_edit(status_msg, f"❌ {esc(friendly_error(e))}", parse_mode=ParseMode.HTML)
    finally:
        _busy_users.discard(tg_id)


def setup_handlers(d: Dispatcher):
    d.message.register(handle_start_command, CommandStart())
    d.message.register(handle_plans_command, Command("tarif", "tariflar", "plans"))
    d.message.register(handle_profile_command, Command("profil", "profile"))
    d.message.register(handle_referral_command, Command("taklif", "referral"))
    d.message.register(handle_help_command, Command("yordam", "help"))
    d.message.register(handle_activate_command, Command("activate"))
    d.message.register(handle_id_command, Command("id"))
    d.callback_query.register(handle_help_callback, F.data == "show_help")
    d.callback_query.register(handle_plans_callback, F.data == "show_plans")
    d.callback_query.register(handle_buy_callback, F.data.startswith("buy:"))
    d.callback_query.register(handle_voice_choice, F.data.startswith("v:"))
    d.message.register(handle_youtube_message, F.text.regexp(YOUTUBE_URL_REGEX))
    d.message.register(handle_video_message, F.video | (F.document & F.document.mime_type.startswith("video/")))


async def start_telegram_bot():
    global bot, dp, bot_task
    if not TELEGRAM_BOT_TOKEN:
        logger.info("TELEGRAM_BOT_TOKEN not set. Telegram bot is in standby.")
        return
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        dp = Dispatcher()
        setup_handlers(dp)
        try:
            await bot.set_my_commands([
                types.BotCommand(command="start", description="Boshlash"),
                types.BotCommand(command="tarif", description="Tariflar va to'lov"),
                types.BotCommand(command="profil", description="Mening hisobim"),
                types.BotCommand(command="taklif", description="Do'st taklif qilish (+1 video)"),
                types.BotCommand(command="yordam", description="Qo'llanma"),
            ])
        except Exception as e:
            logger.warning(f"set_my_commands failed: {e}")
        logger.info(f"Starting Telegram bot polling (@{TELEGRAM_BOT_USERNAME})...")
        bot_task = asyncio.create_task(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()))
    except Exception as e:
        logger.error(f"Failed to start Telegram bot: {e}")


async def stop_telegram_bot():
    global bot, bot_task
    if bot_task:
        bot_task.cancel()
    if bot:
        await bot.session.close()
        logger.info("Telegram bot session closed.")
