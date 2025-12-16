#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import re
from typing import Dict, Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# ========= تنظیمات متغیرهای محیطی =========

BOT_TOKEN = os.getenv("BOT_TOKEN")  # توکن ربات
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده است.")

# آدرس کانال (مثل @NuLimit یا آیدی عددی کانال)
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@NuLimit")

# سکرت ساده برای API
API_SECRET = os.getenv("API_SECRET", "change_this_secret")

# لیست ادمین‌ها (Telegram user_idها، با ویرگول جدا شده)
ADMIN_IDS: set[int] = set()
_admin_str = os.getenv("ADMIN_IDS", "").replace(" ", "")
if _admin_str:
    try:
        ADMIN_IDS = {int(x) for x in _admin_str.split(",") if x}
    except ValueError:
        # اگر فرمت اشتباه بود، نادیده می‌گیریم
        pass

# تنظیمات لاگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# برای هر app_id چه user_idی قبلاً با ربات کار کرده
APP_USER: Dict[str, int] = {}

# وضعیت تایید هر app_id
APP_VERIFIED: Dict[str, bool] = {}

# منبع عضویت برای هر app_id:
# 0 = نامشخص / فعلاً معلوم نیست
# 1 = از اولین /start عضو کانال بوده
# 2 = اول عضو نبوده، بعداً عضو شده (کاربر جدید از طریق ربات)
APP_JOIN_SOURCE: Dict[str, int] = {}

# خود Application ربات (برای استفاده داخل FastAPI)
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


# ========= ربات تلگرام =========


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    text = (message.text or "").strip()

    # اجازه بده /start@BotName join_xxx هم کار کند
    m = re.match(r"^/start(?:@\w+)?\s+join_(.+)$", text)
    if not m:
        await message.reply_text(
            "سلام! برای تأیید عضویت، از داخل اپلیکیشن روی لینک این ربات بزن."
        )
        return

    app_id = m.group(1).strip()
    logger.info("Received /start for app_id=%s from user=%s", app_id, user.id)

    # این app_id به این user تلگرام وصل می‌شود
    APP_USER[app_id] = user.id

    # وضعیت قبلی این app_id (برای تشخیص جدید/قدیمی)
    prev_verified = APP_VERIFIED.get(app_id, None)  # None = اولین بار

    # چک عضویت کاربر در کانال همین الان
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

        # تشخیص نوع کاربر و نوتیفیکیشن به ادمین
        username = f"@{user.username}" if user.username else "-"
        if prev_verified is None:
            # اولین بار این app_id را می‌بینیم و کاربر همین الان عضو است:
            # یعنی از قبل عضو کانال بوده
            APP_JOIN_SOURCE[app_id] = 1
            notif = (
                "✅ کاربر قدیم عضو کانال شده بود؛ عضویت تأیید شد\n\n"
                f"user_id: {user.id}\n"
                f"username: {username}\n"
                f"app_id: {app_id}"
            )
            await notify_admins(notif, context.application)

        elif prev_verified is False:
            # قبلاً این app_id را دیده بودیم و عضو نبود؛ الان شده عضو:
            # کاربر جدید که بعداً عضو شده
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

        # لینک کانال را بر اساس REQUIRED_CHANNEL می‌سازیم
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # لاگ خطا همراه با استک
    logger.error("Exception while handling update:", exc_info=context.error)


# ========= دستورات ادمین =========


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش کیبورد پایین صفحه فقط برای ادمین‌ها."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_admin(user.id):
        return

    keyboard = [
        [KeyboardButton("/stats")],
        [KeyboardButton("/sendAll")],
        [KeyboardButton("/send")],
    ]
    await msg.reply_text(
        "منوی ادمین:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """آمار ساده‌ی ربات (فقط در حافظه فعلی)."""
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
    """
    ارسال پیام گروهی به همه کاربرانی که حداقل یک‌بار با ربات کار کرده‌اند.
    دو روش:
      1) ریپلای روی یک پیام + /sendAll
      2) /sendAll متن پیام
    """
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_admin(user.id):
        return

    # تعیین متن
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
            # مقداری تاخیر برای جلوگیری از Flood
            await asyncio.sleep(0.05)  # حدود ۲۰ پیام در ثانیه
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
    """
    ارسال تست به یک user_id خاص برای دیباگ.
    /send <user_id> متن پیام
    """
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


# ========= FastAPI برای endpoint /check_join =========

api = FastAPI()


@api.get("/check_join")
async def check_join(app_id: str, secret: str):
    # امنیت ساده با secret
    if secret != API_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    global TELEGRAM_APP
    if TELEGRAM_APP is None:
        logger.error("Telegram Application not initialized")
        raise HTTPException(status_code=500, detail="bot_not_ready")

    user_id = APP_USER.get(app_id)
    if not user_id:
        # هنوز هیچ‌وقت /start join_<app_id> برای این app_id نیامده
        return JSONResponse({"verified": False})

    prev_verified = APP_VERIFIED.get(app_id, None)

    # هر بار زنده از تلگرام می‌پرسیم که الان عضو هست یا نه
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

    # اگر قبلاً عضو نبوده و الان عضو شده: کاربر جدید
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


# ========= اجرای هم‌زمان ربات و FastAPI =========


async def setup_telegram_application() -> Application:
    """
    ساخت و راه‌اندازی Application تلگرام (بدون بستن).
    """
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
    await application.start()

    # در نسخه‌های جدید python-telegram-bot، این Updater معمولاً وجود دارد
    if application.updater:
        await application.updater.start_polling()

    logger.info("Telegram join-bot started.")
    return application


async def main():
    # ربات تلگرام را راه‌اندازی می‌کنیم
    application = await setup_telegram_application()

    # FastAPI با uvicorn
    import uvicorn

    config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    try:
        # تا زمانی که uvicorn در حال اجراست، این تابع برنمی‌گردد
        await server.serve()
    finally:
        # شات‌داون تمیز ربات تلگرام
        logger.info("Shutting down Telegram bot...")
        try:
            if application.updater:
                await application.updater.stop()
        except Exception as e:
            logger.warning("خطا در توقف updater: %s", e)

        try:
            await application.stop()
            await application.shutdown()
        except Exception as e:
            logger.warning("خطا در shutdown ربات: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
