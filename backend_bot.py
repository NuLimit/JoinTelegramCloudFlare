#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import re
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# ========= تنظیمات متغیرهای محیطی =========

BOT_TOKEN = os.getenv("BOT_TOKEN")  # توکن ربات join
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده است.")

REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@NuLimit")
API_SECRET = os.getenv("API_SECRET", "change_this_secret")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# برای هر app_id چه user_idی قبلاً با ربات کار کرده
APP_USER: Dict[str, int] = {}

# برای لاگ و دیباگ
APP_VERIFIED: Dict[str, bool] = {}

# خود Application ربات (برای استفاده داخل FastAPI)
TELEGRAM_APP: Optional[Application] = None

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

    if status in ("member", "administrator", "creator"):
        APP_VERIFIED[app_id] = True
        await message.reply_text(
            "عضویت‌ات در کانال تأیید شد ✅\n"
            "حالا به اپلیکیشن برگرد و روی «چک عضویت» بزن."
        )
    else:
        APP_VERIFIED[app_id] = False

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("عضویت در کانال NuLimit", url="https://t.me/NuLimit")]
        ])

        await message.reply_text(
            "هنوز عضو کانال نیستی.\n"
            "برای عضویت روی دکمهٔ زیر بزن، بعد برگرد اپ و دوباره روی لینک ربات بزن.",
            reply_markup=keyboard,
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)


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

    # اینجا هر بار زنده از تلگرام می‌پرسیم که الان عضو هست یا نه
    try:
        member = await TELEGRAM_APP.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        status = member.status
        verified = status in ("member", "administrator", "creator")
        APP_VERIFIED[app_id] = verified
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

    return JSONResponse({"verified": verified})


# ========= اجرای هم‌زمان ربات و FastAPI =========


async def main():
    global TELEGRAM_APP

    application = Application.builder().token(BOT_TOKEN).build()
    TELEGRAM_APP = application

    application.add_handler(CommandHandler("start", start))
    application.add_error_handler(error_handler)

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("Telegram join-bot started.")

    # FastAPI با uvicorn
    import uvicorn

    config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    await asyncio.gather(server_task)


if __name__ == "__main__":
    asyncio.run(main())

