"""Mini App (Telegram Web App): Profile, Ranks, Tours, Trophies, Zone.

Read-only view of existing data, served by an aiohttp server inside the bot process.
Integration (bot.py):  import miniapp ... miniapp.register(app, BOT_TOKEN)

Env:
  PORT                       set automatically by Railway
  MINIAPP_URL                public https URL of this service (enables the /app button)
  MINIAPP_COMMUNITY_URL      optional invite link shown in the Zone tab
  MINIAPP_COMMUNITY_NAME     optional label for that link
  MINIAPP_NAME               app name shown everywhere (default: UltimateCric)
  MINIAPP_ENABLED=0          turn the web server off
"""

import logging
import os

logger = logging.getLogger(__name__)


async def cmd_app(update, context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    name = os.environ.get("MINIAPP_NAME", "UltimateCric").strip() or "UltimateCric"
    url = os.environ.get("MINIAPP_URL", "").strip()
    if not url.startswith("https://"):
        await update.message.reply_text("📱 The Mini App isn't configured yet. (Owner: set MINIAPP_URL to the public https URL.)")
        return
    if update.effective_chat.type == "private":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🏏 Open {name}", web_app=WebAppInfo(url=url))]])
        await update.message.reply_text(f"📱 <b>{name} Mini App</b>\nProfile · Ranks · Tours · Trophies", parse_mode="HTML", reply_markup=kb)
    else:
        me = await context.bot.get_me()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📱 Open in private chat", url=f"https://t.me/{me.username}?start=app")]])
        await update.message.reply_text("📱 The Mini App opens in a private chat with the bot.", reply_markup=kb)


def register(app, bot_token: str) -> None:
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("app", cmd_app))
    if os.environ.get("MINIAPP_ENABLED", "1") == "0":
        return
    previous_init, previous_stop = app.post_init, app.post_shutdown
    state = {}

    async def _post_init(application):
        if previous_init:
            await previous_init(application)
        try:
            from . import server
            me = await application.bot.get_me()
            state["runner"] = await server.start(bot_token, me.username, int(os.environ.get("PORT", "8080")), application.bot)
        except Exception:
            logger.exception("Mini App server failed to start (the bot keeps running)")

    async def _post_shutdown(application):
        if state.get("runner"):
            await state["runner"].cleanup()
        if previous_stop:
            await previous_stop(application)

    app.post_init, app.post_shutdown = _post_init, _post_shutdown
