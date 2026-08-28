import os
import sqlite3
import logging
import threading
import time
import asyncio
import requests

from fastapi import FastAPI
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

# ----------------- CONFIGURATION -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8938462906:AAFYk6G_7xjOZ2NeHY_Zz23ZIBKOlxRLoYo")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8423151783"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

REQUIRED_CHANNELS = [
    {
        "name": "Channel 1 📢", 
        "id": -1001970695975, 
        "url": "https://t.me/+yMFfdb1CKJpkOWE1"
    },
    {
        "name": "HMM Tricks 📢", 
        "id": "@hmm_tricks", 
        "url": "https://t.me/hmm_tricks"
    }
]

CUSTOM_EMOJI_IDS = {
    "ref_link": "5271604874419647061",
    "stats": "5231200819986047254",
    "claim": "5449816553727998023",
    "referrals": "5985525762973768278",
    "check": "6028565819225542441",
    "channel": "6035277294036061660",
    "admin_add": "6034851808805918335",
    "stock": "5294118392905623955",
    "broadcast": "5780405967527089720",
    "users": "5985525762973768278",
}

MESSAGE_FIRE_EFFECT_ID = "5104841245755180586"

MESSAGE_EMOJI_IDS = {
    "party": "5989848973974704652", "stop": "5974083768233760323", "wave": "4983292515932177130", 
    "check": "5980930633298350051", "cross": "6158841463032519010", "warning": "5787656288934564517", 
    "link": "5292122921035133343", "stats": "5431577498364158238", "people": "5402211308017840657", 
    "box": "5415750994849976302", "green": "5416081784641168838", "red": "5420323339723881652", 
    "yellow": "5789570564448326827", "gear": "5341715473882955310", "plus": "5226945370684140473", 
    "megaphone": "5836698068061261980", "hourglass": "5451646226975955576", "repeat": "5264727218734524899", 
    "fire": "5424972470023104089",
}

MESSAGE_EMOJI_MAP = {
    "🎉": "party", "🛑": "stop", "👋": "wave", "✅": "check",
    "❌": "cross", "⚠️": "warning", "🔗": "link", "📊": "stats",
    "👥": "people", "📦": "box", "🟢": "green", "🔴": "red",
    "🟡": "yellow", "⚙️": "gear", "➕": "plus", "📢": "megaphone",
    "⏳": "hourglass", "♻️": "repeat", "🔥": "fire",
}

DB_FILE = "bot_data.db"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# ----------------- DATABASE -----------------
def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=20.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            referrer_id INTEGER,
            credits INTEGER DEFAULT 1,
            claimed_count INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE,
            is_claimed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ----------------- HELPER FUNCTIONS -----------------
def premium_button(text, callback_data=None, style=None, emoji_key=None, url=None):
    kwargs = {"text": text, "callback_data": callback_data, "style": style}
    if url is not None:
        kwargs.pop("callback_data", None)
        kwargs["url"] = url
    emoji_id = CUSTOM_EMOJI_IDS.get(emoji_key or "")
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**kwargs)

def premium_message(text):
    entities = []
    for emoji, key in sorted(MESSAGE_EMOJI_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        emoji_id = MESSAGE_EMOJI_IDS.get(key)
        if not emoji_id:
            continue
        start = 0
        while True:
            pos = text.find(emoji, start)
            if pos < 0:
                break
            offset = len(text[:pos].encode("utf-16-le")) // 2
            length = len(emoji.encode("utf-16-le")) // 2
            entities.append(MessageEntity(
                type="custom_emoji",
                offset=offset,
                length=length,
                custom_emoji_id=emoji_id,
            ))
            start = pos + len(emoji)
    entities.sort(key=lambda e: e.offset)
    return text, entities

async def reply_premium(message, text, **kwargs):
    text, entities = premium_message(text)
    kwargs.pop("parse_mode", None)
    return await message.reply_text(text, entities=entities or None, **kwargs)

async def send_premium(bot, chat_id, text, **kwargs):
    text, entities = premium_message(text)
    kwargs.pop("parse_mode", None)
    return await bot.send_message(chat_id=chat_id, text=text, entities=entities or None, **kwargs)

async def safe_answer(query):
    try:
        await query.answer()
    except Exception:
        pass

async def is_subscribed(user_id: int, bot) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            pass
    return True

# ----------------- KEYBOARDS -----------------
def get_join_keyboard():
    keyboard = []
    row = []
    for ch in REQUIRED_CHANNELS:
        row.append(premium_button(ch["name"], None, "primary", "channel", url=ch["url"]))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([premium_button("CHECK JOINED", "check_join", "success", "check")])
    return InlineKeyboardMarkup(keyboard)

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [
            premium_button("My Referral Link", "ref_link", "primary", "ref_link"),
            premium_button("My Stats", "my_stats", "success", "stats"),
        ],
        [
            premium_button("Claim Agent", "claim_agent", "success", "claim"),
            premium_button("My Referrals", "my_referrals", "primary", "referrals"),
        ],
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [
            premium_button("📦 Total Stock", "admin_stock", "primary", "stock"),
            premium_button("➕ Add Number", "admin_add_num", "success", "admin_add"),
        ],
        [
            premium_button("👥 Total Users", "admin_users", "primary", "users"),
            premium_button("📢 Broadcast", "admin_broadcast", "success", "broadcast"),
        ],
    ])

# ----------------- BOT HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    args = context.args

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, claimed_count, credits FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()

    if not user:
        referrer = None
        if args and args[0].isdigit():
            ref_candidate = int(args[0])
            if ref_candidate != user_id:
                referrer = ref_candidate
                c.execute("UPDATE users SET credits = credits + 1 WHERE user_id = ?", (referrer,))
                try:
                    await send_premium(context.bot, referrer, "🎉 Someone joined via your link! You received +1 Agent Claim credit.")
                except Exception:
                    pass
        c.execute("INSERT INTO users (user_id, referrer_id, credits, claimed_count) VALUES (?, ?, 1, 0)", (user_id, referrer))
        conn.commit()
    elif user[1] == 0 and user[2] == 0:
        c.execute("UPDATE users SET credits = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    conn.close()

    if not await is_subscribed(user_id, context.bot):
        text = "🔥🔥🔥\n🛑 Must Join All Channels To Claim Agent Numbers!\n\nClick the buttons below to join, then tap CHECK JOINED.\n🔥🔥🔥"
        await reply_premium(update.message, text, reply_markup=get_join_keyboard(), disable_web_page_preview=True)
        return

    await reply_premium(update.message, "🔥🔥🔥\n👋 Welcome to the Agent Bot! Choose an option below:\n🔥🔥🔥", reply_markup=get_main_keyboard(), message_effect_id=MESSAGE_FIRE_EFFECT_ID, disable_web_page_preview=True)

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id

    if await is_subscribed(user_id, context.bot):
        success_text, success_entities = premium_message("✅ Verification successful! Choose an option below:")
        await query.message.edit_text(success_text, entities=success_entities or None, reply_markup=get_main_keyboard(), disable_web_page_preview=True)
    else:
        await reply_premium(query.message, "❌ You haven't joined all required channels yet. Please join both and retry.", reply_markup=get_join_keyboard(), disable_web_page_preview=True)

async def claim_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id

    if not await is_subscribed(user_id, context.bot):
        await reply_premium(query.message, "🛑 You must stay joined in all channels to claim!", reply_markup=get_join_keyboard(), disable_web_page_preview=True)
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    credits = row[0] if row else 0

    if credits <= 0:
        bot_user = await context.bot.get_me()
        ref_url = f"https://t.me/{bot_user.username}?start={user_id}"
        conn.close()
        await reply_premium(query.message, f"❌ No claims remaining!\n\nInvite 1 friend to get 1 more Agent Number.\n\nYour Referral Link:\n{ref_url}", disable_web_page_preview=True)
        return

    c.execute("SELECT number FROM agents ORDER BY RANDOM() LIMIT 1")
    agent_row = c.fetchone()

    if not agent_row:
        conn.close()
        await reply_premium(query.message, "⚠️ No agent numbers available in stock right now. Contact admin!")
        return

    selected_number = agent_row[0]
    c.execute("UPDATE users SET credits = credits - 1, claimed_count = claimed_count + 1 WHERE user_id = ? AND credits > 0", (user_id,))
    conn.commit()
    conn.close()

    await reply_premium(query.message, f"🎉 Your Agent Link:\nhttps://wa.me/{selected_number}?text=Hello\n\nInvite 1 more friend to claim another!", disable_web_page_preview=True)

async def user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT credits, claimed_count FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()

    credits = row[0] if row else 0
    claimed = row[1] if row else 0
    await reply_premium(query.message, f"📊 *Your Statistics*\n\n🟢 Available Credits: {credits}\n📦 Total Claimed: {claimed}")

async def my_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
    ref_count = c.fetchone()[0]
    conn.close()

    await reply_premium(query.message, f"👥 *Referral Summary*\n\nTotal Friends Invited: {ref_count}")

async def ref_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    user_id = update.effective_user.id

    bot_user = await context.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={user_id}"
    await reply_premium(query.message, f"🔗 *Your Referral Link:*\n`{link}`\n\nShare this link with your friends. When they join, you get 1 Agent Claim Credit!", disable_web_page_preview=True)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    await reply_premium(update.message, "⚙️ *Welcome to the Admin Dashboard!*\nSelect an action below:", reply_markup=get_admin_keyboard())

async def addbulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    text_to_process = ""
    if update.message.reply_to_message and update.message.reply_to_message.text:
        text_to_process = update.message.reply_to_message.text
    elif update.message.text:
        parts = update.message.text.split(maxsplit=1)
        if len(parts) > 1:
            text_to_process = parts[1]

    if not text_to_process.strip():
        await update.message.reply_text("⚠️ Send numbers directly with `/addbulk` or reply to a list of numbers.", parse_mode="Markdown")
        return

    raw_numbers = [n.strip() for n in text_to_process.replace(",", "\n").replace(" ", "\n").split("\n") if n.strip() and not n.startswith("/")]
    if not raw_numbers:
        await update.message.reply_text("❌ No valid numbers found.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.executemany("INSERT OR IGNORE INTO agents (number) VALUES (?)", [(n,) for n in raw_numbers])
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Processed {len(raw_numbers)} numbers!", parse_mode="Markdown")

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    data = query.data
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if data == "admin_stock":
        c.execute("SELECT COUNT(*) FROM agents")
        count = c.fetchone()[0]
        await reply_premium(query.message, f"📦 *Total Stock in Database:* {count}")
    elif data == "admin_users":
        c.execute("SELECT COUNT(*) FROM users")
        count = c.fetchone()[0]
        await reply_premium(query.message, f"👥 *Total Registered Users:* {count}")
    elif data == "admin_add_num":
        await query.message.reply_text("⚠️ Use `/addbulk` with numbers to import.", parse_mode="Markdown")
    elif data == "admin_broadcast":
        context.user_data["admin_state"] = "awaiting_broadcast"
        await reply_premium(query.message, "📢 Send the message you want to broadcast to all users:")
    conn.close()

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID or not update.message.text:
        return

    if context.user_data.get("admin_state") == "awaiting_broadcast":
        context.user_data["admin_state"] = None
        text = update.message.text.strip()

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT user_id FROM users")
        users = [r[0] for r in c.fetchall()]
        conn.close()

        sent = 0
        for uid in users:
            try:
                await send_premium(context.bot, uid, f"📢 *Broadcast Message:*\n\n{text}", disable_web_page_preview=True)
                sent += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass

        await reply_premium(update.message, f"✅ Broadcast sent to {sent} users!")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err_str = str(context.error).lower()
    if "readerror" in err_str or "query is too old" in err_str or "timed out" in err_str:
        return
    logging.warning(f"Update {update} caused error {context.error}")

# ----------------- FASTAPI SERVER & PING -----------------
api_app = FastAPI()

@api_app.api_route("/", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "service": "Telegram Agent Bot 24/7"}

def run_fastapi():
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(api_app, host="0.0.0.0", port=port, log_level="warning")

def keep_alive_ping():
    time.sleep(15)
    while True:
        if RENDER_URL and "localhost" not in RENDER_URL:
            try:
                requests.get(RENDER_URL, timeout=10)
                logging.info("[KEEP-ALIVE] Ping sent to keep server alive.")
            except Exception:
                pass
        time.sleep(480)

# ----------------- MAIN RUNNER -----------------
def main():
    print("🚀 Starting Web Server & Ping...")
    t_api = threading.Thread(target=run_fastapi, daemon=True)
    t_api.start()

    t_ping = threading.Thread(target=keep_alive_ping, daemon=True)
    t_ping.start()

    print("🚀 Starting Bot Polling...")
    request = HTTPXRequest(
        connection_pool_size=32,
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=30.0,
    )
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("addbulk", addbulk_command))

    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(claim_agent, pattern="^claim_agent$"))
    application.add_handler(CallbackQueryHandler(user_stats, pattern="^my_stats$"))
    application.add_handler(CallbackQueryHandler(my_referrals, pattern="^my_referrals$"))
    application.add_handler(CallbackQueryHandler(ref_link, pattern="^ref_link$"))

    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text))

    application.add_error_handler(error_handler)
    print("✅ Bot is Online and Listening!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
