import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Union

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    ChatPermissions,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes,
)

# -------------------------------------------------------------------
# Configuration & DB
# -------------------------------------------------------------------
CONFIG_FILE = "config.json"
DB_FILE = "db.json"

with open(CONFIG_FILE) as f:
    config = json.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
GROUP_CHAT_ID = config["GROUP_CHAT_ID"]

if not os.path.exists(DB_FILE):
    with open(DB_FILE, "w") as f:
        json.dump({"prefixes": [], "mutes": []}, f)

def load_db():
    with open(DB_FILE) as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# -------------------------------------------------------------------
# Prices and labels
# -------------------------------------------------------------------
PREFIX_PRICES = {
    "10min": 50,
    "1hour": 80,
    "5hours": 150,
    "10hours": 250,
    "24hours": 350,
    "forever": 400,
}

MUTE_PRICES = {
    "10min": 50,
    "1hour": 100,
    "5hours": 200,
    "10hours": 250,
    "24hours": 300,
    "forever": 1000,
}

UNMUTE_PRICE = 70

DURATION_LABELS = {
    "10min": "10 minutes",
    "1hour": "1 hour",
    "5hours": "5 hours",
    "10hours": "10 hours",
    "24hours": "24 hours",
    "forever": "Forever",
}

DURATION_SECONDS = {
    "10min": 600,
    "1hour": 3600,
    "5hours": 18000,
    "10hours": 36000,
    "24hours": 86400,
}

# -------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------
async def delayed_task(delay: float, coro):
    """Run a coroutine after `delay` seconds."""
    await asyncio.sleep(delay)
    await coro()

async def resolve_user(text: str, context: ContextTypes.DEFAULT_TYPE) -> Union[int, None]:
    """
    Resolve a user by @username or numeric ID.
    Returns user_id if found in the group, else None.
    """
    text = text.strip()
    # Remove leading @ if present
    if text.startswith("@"):
        username = text[1:]
    else:
        username = text

    # Case 1: numeric ID
    try:
        user_id = int(text)
        await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return user_id
    except ValueError:
        pass
    except Exception:
        # user not in group or other error, fallback to username resolution
        pass

    # Case 2: username
    try:
        # get_chat with @username returns the user's Chat object
        user = await context.bot.get_chat(f"@{username}")
        # Verify membership in the group
        await context.bot.get_chat_member(GROUP_CHAT_ID, user.id)
        return user.id
    except Exception as e:
        logging.error(f"Resolve user failed: {e}")
        return None

async def set_prefix(context: ContextTypes.DEFAULT_TYPE, user_id: int, title: str, expires_in: Optional[int]):
    """Promote user with a custom title; schedule demotion if temporary."""
    await context.bot.promote_chat_member(
        chat_id=GROUP_CHAT_ID,
        user_id=user_id,
        is_anonymous=False,
        can_manage_chat=False,
        can_change_info=False,
        can_delete_messages=False,
        can_invite_users=False,
        can_restrict_members=False,
        can_pin_messages=False,
        can_promote_members=False,
        can_manage_video_chats=False,
        can_manage_topics=False,
    )
    await context.bot.set_chat_administrator_custom_title(
        chat_id=GROUP_CHAT_ID,
        user_id=user_id,
        custom_title=title,
    )

    if expires_in:
        async def demote():
            try:
                await context.bot.promote_chat_member(
                    chat_id=GROUP_CHAT_ID,
                    user_id=user_id,
                    is_anonymous=False,
                    can_manage_chat=False,
                    can_change_info=False,
                    can_delete_messages=False,
                    can_invite_users=False,
                    can_restrict_members=False,
                    can_pin_messages=False,
                    can_promote_members=False,
                    can_manage_video_chats=False,
                    can_manage_topics=False,
                )
                # Clean DB
                db = load_db()
                db["prefixes"] = [
                    p for p in db["prefixes"]
                    if not (p["user_id"] == user_id and p["title"] == title)
                ]
                save_db(db)
            except Exception as e:
                logging.error(f"Demotion error: {e}")

        asyncio.create_task(delayed_task(expires_in, demote))

async def mute_user(context: ContextTypes.DEFAULT_TYPE, target_id: int, muter_id: int, duration_key: str):
    """Apply mute restriction and store record."""
    now = datetime.utcnow()
    if duration_key == "forever":
        until_date = None
        until_db = None
    else:
        seconds = DURATION_SECONDS[duration_key]
        until_date = int((now + timedelta(seconds=seconds)).timestamp())
        until_db = until_date

    await context.bot.restrict_chat_member(
        chat_id=GROUP_CHAT_ID,
        user_id=target_id,
        permissions=ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        ),
        until_date=until_date,
    )

    db = load_db()
    # Remove any existing mute for this user (avoid duplicates)
    db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
    db["mutes"].append({
        "target_id": target_id,
        "muter_id": muter_id,
        "duration": duration_key,
        "until_date": until_db,
        "purchase_id": f"mute_{duration_key}_{target_id}",
    })
    save_db(db)

    # Schedule removal of the DB record when mute expires (temporary only)
    if duration_key != "forever":
        async def cleanup():
            db = load_db()
            db["mutes"] = [
                m for m in db["mutes"]
                if not (m["target_id"] == target_id and m["until_date"] == until_db)
            ]
            save_db(db)

        asyncio.create_task(delayed_task(seconds, cleanup))

async def unmute_user(context: ContextTypes.DEFAULT_TYPE, target_id: int):
    """Lift all restrictions."""
    await context.bot.restrict_chat_member(
        chat_id=GROUP_CHAT_ID,
        user_id=target_id,
        permissions=ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=False,
            can_invite_users=True,
            can_pin_messages=False,
        ),
        until_date=0,  # lift all
    )
    db = load_db()
    db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
    save_db(db)

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send a message to all group admins (in private chat)."""
    try:
        admins = await context.bot.get_chat_administrators(GROUP_CHAT_ID)
        for admin in admins:
            try:
                await context.bot.send_message(chat_id=admin.user.id, text=text)
            except Exception:
                pass
    except Exception as e:
        logging.error(f"Failed to notify admins: {e}")

# -------------------------------------------------------------------
# Menu handlers
# -------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main control panel (private chat only)."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please use /start in a private chat with me.")
        return

    keyboard = [
        [InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("🛒 Shop", callback_data="shop")],
    ]
    await update.message.reply_text(
        "🎛️ *Control Panel*\nChoose a section:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback to go back to main menu."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("🛒 Shop", callback_data="shop")],
    ]
    await query.edit_message_text(
        "🎛️ *Control Panel*\nChoose a section:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    db = load_db()
    prefix = next((p for p in db["prefixes"] if p["user_id"] == user_id), None)
    mutes = [m for m in db["mutes"] if m["muter_id"] == user_id]

    text = "👤 *Your Profile*\n\n"
    if prefix:
        if prefix.get("expires_at"):
            expire_time = datetime.fromtimestamp(prefix["expires_at"])
            remaining = expire_time - datetime.now()
            if remaining.total_seconds() > 0:
                text += f"🏷️ Prefix: {prefix['title']} (expires in {str(remaining).split('.')[0]})\n"
            else:
                text += "🏷️ Prefix: expired\n"
        else:
            text += f"🏷️ Prefix: {prefix['title']} (forever)\n"
    else:
        text += "🏷️ No active prefix\n"

    if mutes:
        text += "\n🔇 *Your purchased mutes:*\n"
        for m in mutes:
            try:
                target = await context.bot.get_chat(m["target_id"])
                name = f"@{target.username}" if target.username else target.first_name
            except:
                name = f"ID {m['target_id']}"
            if m.get("until_date"):
                exp = datetime.fromtimestamp(m["until_date"])
                remaining = exp - datetime.now()
                if remaining.total_seconds() > 0:
                    text += f"  → {name}: {DURATION_LABELS[m['duration']]} (until {exp.strftime('%H:%M')})\n"
                else:
                    text += f"  → {name}: expired\n"
            else:
                text += f"  → {name}: Forever\n"
    else:
        text += "\n🔇 No mutes purchased"

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🏷️ Prefix", callback_data="shop_prefix")],
        [InlineKeyboardButton("🔇 Mute", callback_data="shop_mute")],
        [InlineKeyboardButton("🔊 Unmute", callback_data="shop_unmute")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ]
    await query.edit_message_text(
        "🛒 *Shop*\nChoose a product:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

# -------------------------------------------------------------------
# Product cards & duration selection
# -------------------------------------------------------------------
async def show_prefix_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🏷️ *Prefix*\n"
        "────────────────\n"
        "Get a green custom title in the group.\n"
        "────────────────\n"
        "Prices:\n"
        "10 min — 50 ⭐\n"
        "1 hour — 80 ⭐\n"
        "5 hours — 150 ⭐\n"
        "10 hours — 250 ⭐\n"
        "24 hours — 350 ⭐\n"
        "Forever — 400 ⭐\n"
        "Select duration:"
    )
    keyboard = [
        [InlineKeyboardButton("10 min · 50⭐", callback_data="prefix_dur_10min")],
        [InlineKeyboardButton("1 hour · 80⭐", callback_data="prefix_dur_1hour")],
        [InlineKeyboardButton("5 hours · 150⭐", callback_data="prefix_dur_5hours")],
        [InlineKeyboardButton("10 hours · 250⭐", callback_data="prefix_dur_10hours")],
        [InlineKeyboardButton("24 hours · 350⭐", callback_data="prefix_dur_24hours")],
        [InlineKeyboardButton("Forever · 400⭐", callback_data="prefix_dur_forever")],
        [InlineKeyboardButton("🔙 Back to shop", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_prefix_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dur = query.data.split("_")[2]
    await query.edit_message_text("💳 Sending invoice...")
    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title="Prefix purchase",
        description=f"Green prefix for {DURATION_LABELS[dur]}",
        payload=f"prefix_{dur}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Prefix", PREFIX_PRICES[dur])],
        start_parameter="prefix",
    )

async def show_mute_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🔇 *Mute*\n"
        "────────────────\n"
        "Mute any user in the group.\n"
        "────────────────\n"
        "Prices:\n"
        "10 min — 50 ⭐\n"
        "1 hour — 100 ⭐\n"
        "5 hours — 200 ⭐\n"
        "10 hours — 250 ⭐\n"
        "24 hours — 300 ⭐\n"
        "Forever — 1000 ⭐\n"
        "Choose duration first:"
    )
    keyboard = [
        [InlineKeyboardButton("10 min · 50⭐", callback_data="mute_dur_10min")],
        [InlineKeyboardButton("1 hour · 100⭐", callback_data="mute_dur_1hour")],
        [InlineKeyboardButton("5 hours · 200⭐", callback_data="mute_dur_5hours")],
        [InlineKeyboardButton("10 hours · 250⭐", callback_data="mute_dur_10hours")],
        [InlineKeyboardButton("24 hours · 300⭐", callback_data="mute_dur_24hours")],
        [InlineKeyboardButton("Forever · 1000⭐", callback_data="mute_dur_forever")],
        [InlineKeyboardButton("🔙 Back to shop", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dur = query.data.split("_")[2]
    context.user_data["pending_mute"] = dur
    await query.edit_message_text(
        "Send the @username or numeric ID of the user you want to mute.\n"
        "Example: `@username` or `123456789`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="shop")]]),
    )

async def show_unmute_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🔊 *Unmute*\n"
        "────────────────\n"
        "Remove mute from a user.\n"
        "Price: 70 ⭐\n"
        "────────────────\n"
        "Send the @username or ID of the person you want to unmute:"
    )
    context.user_data["pending_unmute"] = True
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="shop")]]),
    )

# -------------------------------------------------------------------
# Target input handler
# -------------------------------------------------------------------
async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process user input for mute/unmute targets."""
    if update.effective_chat.type != "private":
        return

    user_data = context.user_data
    msg_text = update.message.text.strip()

    if "pending_mute" in user_data:
        dur = user_data.pop("pending_mute")
        target_id = await resolve_user(msg_text, context)
        if not target_id:
            await update.message.reply_text("❌ User not found in the group. Please try again.")
            return

        db = load_db()
        if any(m["target_id"] == target_id for m in db["mutes"]):
            await update.message.reply_text("🔇 This user is already muted.")
            return

        amount = MUTE_PRICES[dur]
        await update.message.reply_invoice(
            title="Mute purchase",
            description=f"Mute user for {DURATION_LABELS[dur]}",
            payload=f"mute_{dur}_{target_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Mute", amount)],
            start_parameter="mute",
        )

    elif "pending_unmute" in user_data:
        del user_data["pending_unmute"]
        target_id = await resolve_user(msg_text, context)
        if not target_id:
            await update.message.reply_text("❌ User not found in the group. Please try again.")
            return

        db = load_db()
        if not any(m["target_id"] == target_id for m in db["mutes"]):
            await update.message.reply_text("🔊 This user is not muted by the bot.")
            return

        await update.message.reply_invoice(
            title="Unmute purchase",
            description="Remove mute from user",
            payload=f"unmute_{target_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Unmute", UNMUTE_PRICE)],
            start_parameter="unmute",
        )

# -------------------------------------------------------------------
# Payment handlers
# -------------------------------------------------------------------
async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    buyer = update.effective_user
    buyer_name = buyer.full_name
    buyer_username = buyer.username
    buyer_mention = f"@{buyer_username}" if buyer_username else buyer_name

    await update.message.reply_text("✅ Payment successful! Processing...")

    if payload.startswith("prefix_"):
        dur = payload.split("_")[1]
        title = "🟢 Premium"
        if dur == "forever":
            expires_in = None
        else:
            expires_in = DURATION_SECONDS[dur]

        await set_prefix(context, buyer.id, title, expires_in)

        db = load_db()
        db["prefixes"].append({
            "user_id": buyer.id,
            "title": title,
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).timestamp() if expires_in else None,
            "purchase_id": payload,
        })
        save_db(db)

        await notify_admins(
            context,
            f"🟢 {buyer_name} ({buyer_mention}) bought a prefix for {DURATION_LABELS[dur]}."
        )
        await context.bot.send_message(
            GROUP_CHAT_ID,
            f"🎉 {buyer_mention} bought a green prefix for {DURATION_LABELS[dur]}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Do the same", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

    elif payload.startswith("mute_"):
        _, dur, target_id_str = payload.split("_")
        target_id = int(target_id_str)
        await mute_user(context, target_id, buyer.id, dur)

        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
        except:
            target_name = f"ID {target_id}"

        await notify_admins(
            context,
            f"🔇 {buyer_name} ({buyer_mention}) muted {target_name} for {DURATION_LABELS[dur]}."
        )
        await context.bot.send_message(
            GROUP_CHAT_ID,
            f"🔇 {buyer_mention} muted {target_name} for {DURATION_LABELS[dur]}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Do the same", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

    elif payload.startswith("unmute_"):
        target_id = int(payload.split("_")[1])
        await unmute_user(context, target_id)

        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
        except:
            target_name = f"ID {target_id}"

        await notify_admins(
            context,
            f"🔊 {buyer_name} ({buyer_mention}) unmuted {target_name}."
        )
        await context.bot.send_message(
            GROUP_CHAT_ID,
            f"🔊 {buyer_mention} unmuted {target_name}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Do the same", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

# -------------------------------------------------------------------
# Callback router
# -------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "profile":
        await show_profile(update, context)
    elif data == "shop":
        await show_shop(update, context)
    elif data == "main_menu":
        await main_menu_callback(update, context)
    elif data == "shop_prefix":
        await show_prefix_card(update, context)
    elif data.startswith("prefix_dur_"):
        await handle_prefix_duration(update, context)
    elif data == "shop_mute":
        await show_mute_card(update, context)
    elif data.startswith("mute_dur_"):
        await handle_mute_duration(update, context)
    elif data == "shop_unmute":
        await show_unmute_card(update, context)

# -------------------------------------------------------------------
# Restore scheduled tasks on startup
# -------------------------------------------------------------------
async def restore_scheduled_jobs(app: Application):
    """Re‑schedule all prefix demotions and mute cleanups."""
    db = load_db()
    now = datetime.utcnow().timestamp()

    for prefix in db["prefixes"]:
        if prefix.get("expires_at"):
            delay = prefix["expires_at"] - now
            if delay > 0:
                async def demote_restored(uid=prefix["user_id"], title=prefix["title"]):
                    try:
                        await app.bot.promote_chat_member(
                            chat_id=GROUP_CHAT_ID,
                            user_id=uid,
                            is_anonymous=False,
                            can_manage_chat=False,
                            can_change_info=False,
                            can_delete_messages=False,
                            can_invite_users=False,
                            can_restrict_members=False,
                            can_pin_messages=False,
                            can_promote_members=False,
                            can_manage_video_chats=False,
                            can_manage_topics=False,
                        )
                        db2 = load_db()
                        db2["prefixes"] = [
                            p for p in db2["prefixes"]
                            if not (p["user_id"] == uid and p["title"] == title)
                        ]
                        save_db(db2)
                    except Exception as e:
                        logging.error(f"Restored demotion error: {e}")
                asyncio.create_task(delayed_task(delay, demote_restored))

    for mute in db["mutes"]:
        if mute.get("until_date"):
            delay = mute["until_date"] - now
            if delay > 0:
                async def cleanup_restored(tid=mute["target_id"], until=mute["until_date"]):
                    db2 = load_db()
                    db2["mutes"] = [
                        m for m in db2["mutes"]
                        if not (m["target_id"] == tid and m["until_date"] == until)
                    ]
                    save_db(db2)
                asyncio.create_task(delayed_task(delay, cleanup_restored))

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_target_input))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Restore scheduled jobs after bot is ready
    async def post_init(application):
        await restore_scheduled_jobs(application)

    app.post_init = post_init

    app.run_polling()

if __name__ == "__main__":
    main()
