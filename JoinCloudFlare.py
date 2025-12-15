# JoinTelegramCloudFlare
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import re
import json
from typing import Dict, Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    Bot,
)
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# ========= تنظیمات متغیرهای محیطی =========

# NOTE: برای Cloudflare Functions، بهتر است از متغیرهای محیطی در تنظیمات Pages استفاده کنید.
BOT_TOKEN = os.getenv("BOT_TOKEN")  # توکن ربات
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده است.")

REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@NuLimit")
API_SECRET = os.getenv("API_SECRET", "change_this_secret")

ADMIN_IDS: set[int] = set()
_admin_str = os.getenv("ADMIN_IDS", "").replace(" ", "")
if _admin_str:
    try:
        ADMIN_IDS = {int(x) for x in _admin_str.split(",") if x}
    except ValueError:
        pass

# تنظیمات لاگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- داده‌های موقت (توجه: با ری‌استارت Cloudflare Functions از بین می‌روند) ---
APP_USER: Dict[str, int] = {}
APP_VERIFIED: Dict[str, bool] = {}
APP_JOIN_SOURCE: Dict[str, int] = {}
# --------------------------------------------------------------------------

TELEGRAM_APP: Optional[Application] = None


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def notify_admins(text: str, app: Application) -> None:
    """ارسال نوتیف ساده برای همه ادمین‌ها (بدون Markdown)."""
    if not ADMIN_IDS:
        return
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.error("خطا در ارسال نوتیفیکیشن به ادمین %s: %s", admin_id, e)


# ========= توابع ربات تلگرام (بدون تغییر در منطق) =========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (منطق تابع start کاملا حفظ شده است) ...
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    text = (message.text or "").strip()

    m = re.match(r"^/start(?:@\w+)?\s+join_(.+)$", text)
    if not m:
        await message.reply_text(
            "سلام! برای تأیید عضویت، از داخل اپلیکیشن روی لینک این ربات بزن."
        )
        return

    app_id = m.group(1).strip()
    logger.info("Received /start for app_id=%s from user=%s", app_id, user.id)

    APP_USER[app_id] = user.id
    prev_verified = APP_VERIFIED.get(app_id, None)

    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user.id)
        status = member.status
        logger.info("User %s status in %s is %s", user.id, REQUIRED_CHANNEL, status)
    except Exception as e:
        logger.error("خطا در getChatMember داخل start: %s", e)
        await message.reply_text(
            "در بررسی عضویت مشکلی پیش اومد. چند لحظه بعد از اپ دوباره امتحان کن."
        )
        return

    is_member_now = status in ("member", "administrator", "creator")

    if is_member_now:
        APP_VERIFIED[app_id] = True

        username = f"@{user.username}" if user.username else "-"
        if prev_verified is None:
            APP_JOIN_SOURCE[app_id] = 1
            notif = (
                "✅ کاربر قدیم عضو کانال شده بود؛ عضویت تأیید شد\n\n"
                f"user_id: {user.id}\n"
                f"username: {username}\n"
                f"app_id: {app_id}"
            )
            await notify_admins(notif, context.application)

        elif prev_verified is False:
            APP_JOIN_SOURCE[app_id] = 2
            notif = (
                "✅ کاربر جدید عضو کانال شد (بعد از کار با ربات)\n\n"
                f"user_id: {user.id}\n"
                f"username: {username}\n"
                f"app_id: {app_id}"
            )
            await notify_admins(notif, context.application)

        await message.reply_text(
            "عضویت‌ات در کانال تأیید شد ✅\n"
            "حالا به اپلیکیشن برگرد و روی «چک عضویت» بزن."
        )

    else:
        APP_VERIFIED[app_id] = False
        APP_JOIN_SOURCE.setdefault(app_id, 0)

        channel_username = REQUIRED_CHANNEL.lstrip("@")
        join_url = f"https://t.me/{channel_username}"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"عضویت در کانال {channel_username}", url=join_url
                    )
                ]
            ]
        )

        await message.reply_text(
            "هنوز عضو کانال نیستی.\n"
            "برای عضویت روی دکمهٔ زیر بزن، بعد برگرد اپ و دوباره روی لینک ربات بزن.",
            reply_markup=keyboard,
        )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (منطق cmd_menu کاملا حفظ شده است) ...
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_admin(user.id):
        return

    keyboard = [
        [KeyboardButton("/stats")],
        [KeyboardButton("/sendall")],
        [KeyboardButton("/send")],
    ]
    await msg.reply_text(
        "منوی ادمین:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (منطق cmd_stats کاملا حفظ شده است) ...
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_admin(user.id):
        return

    total_app_ids = len(APP_USER)
    unique_users = {uid for uid in APP_USER.values() if uid}
    total_users = len(unique_users)

    verified_app_ids = sum(1 for v in APP_VERIFIED.values() if v)
    verified_users = len(
        {
            APP_USER[app_id]
            for app_id, v in APP_VERIFIED.items()
            if v and app_id in APP_USER
        }
    )

    text = (
        "📊 آمار ربات Join\n\n"
        f"🔹 تعداد app_idهای ثبت‌شده: {total_app_ids}\n"
        f"👥 تعداد کاربران یکتا که تا حالا از ربات استفاده کرده‌اند: {total_users}\n"
        f"✅ تعداد app_idهای تأیید‌شده: {verified_app_ids}\n"
        f"✅ کاربران یکتای تأیید‌شده (حداقل یک app_id تأیید شده): {verified_users}\n\n"
        "نکته: این آمار فقط تا وقتی پروسهٔ فعلی در حال اجراست نگه‌داری می‌شود "
        "و با ری‌استارت سرور صفر می‌شود (چون دیتابیس نداریم)."
    )
    await msg.reply_text(text)


async def cmd_send_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (منطق cmd_send_all کاملا حفظ شده است) ...
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_admin(user.id):
        return

    if msg.reply_to_message and msg.reply_to_message.text:
        text = msg.reply_to_message.text
    else:
        full_text = msg.text or ""
        m = re.match(r"^/sendall(?:@\w+)?\s+(.+)$", full_text, flags=re.S | re.I)
        if not m:
            await msg.reply_text(
                "برای ارسال پیام به همه:\n"
                "۱) روی متنی که می‌خواهی بفرستی ریپلای بزن و /sendAll بفرست.\n"
                "۲) یا این‌طور بزن:\n"
                "/sendAll متن پیام"
            )
            return
        text = m.group(1).strip()

    if not text:
        await msg.reply_text("متن پیام خالی است.")
        return

    user_ids = sorted({uid for uid in APP_USER.values() if uid})
    if not user_ids:
        await msg.reply_text("هنوز هیچ کاربری با ربات کار نکرده.")
        return

    await msg.reply_text(
        f"ارسال پیام به {len(user_ids)} کاربر شروع شد، کمی صبر کن..."
    )

    sent = 0
    failed = 0
    errors: List[str] = []

    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            err = f"{uid}: {e}"
            errors.append(err)
            logger.warning("خطا در sendAll به %s: %s", uid, e)

    summary = (
        "ارسال پیام گروهی تمام شد.\n"
        f"✅ موفق: {sent}\n"
        f"❌ ناموفق: {failed}"
    )
    if errors:
        summary += "\n\nنمونه خطاها (حداکثر ۱۰ مورد):\n" + "\n".join(errors[:10])

    await msg.reply_text(summary)


async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (منطق cmd_send کاملا حفظ شده است) ...
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_admin(user.id):
        return

    if len(context.args) < 2:
        await msg.reply_text(
            "استفاده:\n/send <user_id> <متن پیام>\n\n"
            "مثال:\n/send 123456789 سلام تست"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await msg.reply_text("user_id باید عدد باشد.")
        return

    text = " ".join(context.args[1:]).strip()
    if not text:
        await msg.reply_text("متن پیام خالی است.")
        return

    try:
        await context.bot.send_message(chat_id=target_id, text=text)
        await msg.reply_text("پیام تست به کاربر ارسال شد (اگر تلگرام اجازه داده باشد).")
    except TelegramError as e:
        await msg.reply_text(f"خطا از طرف تلگرام:\n{e}")
    except Exception as e:
        await msg.reply_text(f"خطای داخلی:\n{e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)

# ========= راه‌اندازی Application تلگرام (بدون Polling) =========

async def setup_telegram_application() -> Application:
    """ساخت و راه‌اندازی Application تلگرام."""
    global TELEGRAM_APP

    application = Application.builder().token(BOT_TOKEN).build()
    TELEGRAM_APP = application

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("sendall", cmd_send_all))
    application.add_handler(CommandHandler("send", cmd_send))
    application.add_error_handler(error_handler)

    await application.initialize()
    # در Webhook فقط application را start می‌کنیم، نیازی به Polling نیست.
    await application.start()
    
    logger.info("Telegram join-bot setup complete.")
    return application

# برنامه را در زمان اجرای ماژول برای اولین بار setup می‌کنیم
asyncio.run(setup_telegram_application())

# ========= FastAPI برای Webhook و endpoint /check_join =========

api = FastAPI()

@api.on_event("startup")
async def startup_event():
    # اطمینان از راه‌اندازی مجدد در صورت نیاز
    if TELEGRAM_APP is None:
        await setup_telegram_application()


@api.post("/bot")
async def telegram_webhook(request: Request):
    """دریافت به‌روزرسانی‌ها از تلگرام."""
    if TELEGRAM_APP is None:
        logger.error("Telegram Application not initialized for webhook.")
        raise HTTPException(status_code=500, detail="bot_not_ready")

    # تلگرام یک JSON می‌فرستد.
    body = await request.json()

    # آپدیت را به Application تلگرام می‌فرستیم تا هندل شود.
    try:
        update = Update.de_json(body, TELEGRAM_APP.bot)
        await TELEGRAM_APP.process_update(update)
    except Exception as e:
        logger.error("خطا در پردازش Webhook: %s", e)
        # تلگرام انتظار پاسخ 200 را دارد
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)

    # پاسخ سریع به تلگرام
    return JSONResponse({"status": "ok"})


@api.get("/check_join")
async def check_join(app_id: str, secret: str):
    # ... (منطق check_join کاملا حفظ شده است) ...
    if secret != API_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    if TELEGRAM_APP is None:
        logger.error("Telegram Application not initialized")
        raise HTTPException(status_code=500, detail="bot_not_ready")

    user_id = APP_USER.get(app_id)
    if not user_id:
        return JSONResponse({"verified": False})

    prev_verified = APP_VERIFIED.get(app_id)

    try:
        member = await TELEGRAM_APP.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        status = member.status
        verified = status in ("member", "administrator", "creator")
        logger.info(
            "Live check for app_id=%s user=%s status=%s verified=%s",
            app_id,
            user_id,
            status,
            verified,
        )
    except Exception as e:
        logger.error("خطا در getChatMember داخل check_join: %s", e)
        verified = False
        status = None

    APP_VERIFIED[app_id] = verified

    if verified and prev_verified is False:
        APP_JOIN_SOURCE[app_id] = 2
        username = "-"
        try:
            chat = await TELEGRAM_APP.bot.get_chat(user_id)
            if chat.username:
                username = f"@{chat.username}"
        except Exception:
            pass

        notif = (
            "✅ کاربر جدید عضو کانال شد (بعد از کار با ربات، در check_join)\n\n"
            f"user_id: {user_id}\n"
            f"username: {username}\n"
            f"app_id: {app_id}"
        )
        await notify_admins(notif, TELEGRAM_APP)

    return JSONResponse({"verified": bool(verified)})


# Endpoint برای اطمینان از سلامت سرویس
@api.get("/health")
async def health_check():
    return JSONResponse({"status": "ok", "app": "telegram-join-bot"})


# بخش اجرای مستقل (دیگر نیازی به uvicorn در اینجا نیست، چون توسط Cloudflare اجرا می‌شود)
if __name__ == "__main__":
    # این بخش فقط برای دیباگ محلی است و در Cloudflare اجرا نخواهد شد
    print("This script is ready for Webhook deployment.")
    print("Run with: uvicorn backend_bot:api --host 0.0.0.0 --port 8000")

