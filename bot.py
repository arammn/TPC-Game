#!/usr/bin/env python3
"""
🌟 Telegram Shop Bot — Stars Edition (FIXED & WORKING)
Prefix • Mute • Unmute | v2.0
"""

import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, ChatPermissions,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler,
    filters, ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
#                   🔧 CONFIGURATION
# ═══════════════════════════════════════════════════════

BOT_TOKEN = "8057185585:AAF_LJKPk1OW3U3x7OqOnl0TO2Dux_2mDM0"   # ← PASTE YOUR TOKEN HERE

DB_DIR     = Path("db")
DB_DIR.mkdir(exist_ok=True)

USERS_F    = DB_DIR / "users.json"
PREFIXES_F = DB_DIR / "prefixes.json"
MUTES_F    = DB_DIR / "mutes.json"
CONFIG_F   = DB_DIR / "config.json"
PENDING_F  = DB_DIR / "pending.json"


# ═══════════════════════════════════════════════════════
#                     💰 PRICING
# ═══════════════════════════════════════════════════════

PREFIX_PLANS = {
    "10m":  {"stars": 1,    "minutes": 10,    "label": "10 минут"},
    "1h":   {"stars": 100,   "minutes": 60,    "label": "1 час"},
    "5h":   {"stars": 150,   "minutes": 300,   "label": "5 часов"},
    "10h":  {"stars": 200,   "minutes": 600,   "label": "10 часов"},
    "24h":  {"stars": 300,   "minutes": 1440,  "label": "24 часа"},
    "inf":  {"stars": 400,   "minutes": None,  "label": "Навсегда ♾️"},
}

MUTE_PLANS = {
    "10m":  {"stars": 1,    "minutes": 10,    "label": "10 минут"},
    "1h":   {"stars": 100,   "minutes": 60,    "label": "1 час"},
    "5h":   {"stars": 150,   "minutes": 300,   "label": "5 часов"},
    "10h":  {"stars": 200,   "minutes": 600,   "label": "10 часов"},
    "24h":  {"stars": 300,   "minutes": 1440,  "label": "24 часа"},
    "inf":  {"stars": 1000,  "minutes": None,  "label": "Навсегда ♾️"},
}

UNMUTE_STARS = 1


# ═══════════════════════════════════════════════════════
#                  🗄️ DATABASE
# ═══════════════════════════════════════════════════════

def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            return {}
    return {}

def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

get_config    = lambda: _load(CONFIG_F)
save_config   = lambda d: _save(CONFIG_F, d)
get_users     = lambda: _load(USERS_F)
save_users    = lambda d: _save(USERS_F, d)
get_prefixes  = lambda: _load(PREFIXES_F)
save_prefixes = lambda d: _save(PREFIXES_F, d)
get_mutes     = lambda: _load(MUTES_F)
save_mutes    = lambda d: _save(MUTES_F, d)
get_pending   = lambda: _load(PENDING_F)
save_pending  = lambda d: _save(PENDING_F, d)

def upsert_user(user_id: int, username: str, first_name: str) -> None:
    users = get_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "username": username,
            "first_name": first_name,
            "stars_spent": 0,
            "purchases": 0,
        }
    else:
        users[uid]["username"] = username
        users[uid]["first_name"] = first_name
    save_users(users)

def get_user(user_id: int) -> dict:
    return get_users().get(str(user_id), {})

def record_purchase(user_id: int, stars: int) -> None:
    users = get_users()
    uid = str(user_id)
    if uid in users:
        users[uid]["stars_spent"] = users[uid].get("stars_spent", 0) + stars
        users[uid]["purchases"] = users[uid].get("purchases", 0) + 1
        save_users(users)

def save_pending_purchase(user_id: int, data: dict) -> None:
    pending = get_pending()
    pending[str(user_id)] = data
    save_pending(pending)

def pop_pending_purchase(user_id: int) -> dict:
    pending = get_pending()
    data = pending.pop(str(user_id), {})
    save_pending(pending)
    return data


# ═══════════════════════════════════════════════════════
#            ✅ GROUP PERMISSIONS CHECK
# ═══════════════════════════════════════════════════════

async def check_bot_permissions(bot, group_id: int) -> tuple:
    """Check if bot has all required permissions in the group."""
    try:
        member = await bot.get_chat_member(group_id, bot.id)
        
        if member.status != "administrator":
            return False, "❌ Бот не является администратором группы"
        
        required = {
            "can_restrict_members": "Блокировка пользователей (для мута)",
            "can_promote_members": "Назначение администраторов (для префиксов)",
        }
        
        missing = []
        for perm, desc in required.items():
            if not getattr(member, perm, False):
                missing.append(desc)
        
        if missing:
            msg = "❌ Недостающие права:\n"
            for m in missing:
                msg += f"  • {m}\n"
            return False, msg
        
        return True, "✅ Все права в порядке"
        
    except Exception as e:
        return False, f"❌ Ошибка: {str(e)}"


# ═══════════════════════════════════════════════════════
#            🎮 SAFE MESSAGE EDITING
# ═══════════════════════════════════════════════════════

async def safe_edit(q, text: str, markup) -> bool:
    """Safely edit a message."""
    try:
        await q.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
        return True
    except TelegramError as e:
        logger.warning(f"Edit failed: {e}")
        try:
            await q.answer("Ошибка. Нажмите /start заново.", show_alert=True)
        except:
            pass
        return False


# ═══════════════════════════════════════════════════════
#              🎨 UI KEYBOARDS & TEXTS
# ═══════════════════════════════════════════════════════

def B(text: str, data: str = None, url: str = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data, url=url)

def KB(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

MAIN_TEXT = (
    "╔════════════════════════════════╗\n"
    "║   🌟  ПАНЕЛЬ УПРАВЛЕНИЯ  🌟   ║\n"
    "╚════════════════════════════════╝\n\n"
    "👋 Добро пожаловать!\n\n"
    "💰 Купи привилегии через\n"
    "Telegram Stars!\n\n"
    "Выбери раздел:"
)

def main_kb() -> InlineKeyboardMarkup:
    return KB(
        [B("👤 Профиль", "profile"),  B("🛒 Магазин", "shop")],
    )

SHOP_TEXT = (
    "╔════════════════════════════════╗\n"
    "║         🛒 МАГАЗИН  🛒        ║\n"
    "╚════════════════════════════════╝\n\n"
    "🏷️  ПРЕФИКС\n"
    "    50–400 ⭐\n\n"
    "🔇 МУТ\n"
    "    50–1000 ⭐\n\n"
    "🔊 СНЯТИЕ МУТА\n"
    "    70 ⭐"
)

def shop_kb() -> InlineKeyboardMarkup:
    return KB(
        [B("🏷️ Префикс", "p_card")],
        [B("🔇 Мут", "m_card")],
        [B("🔊 Снятие мута", "u_card")],
        [B("🔙 Назад", "main")],
    )

PREFIX_CARD = (
    "╔════════════════════════════════╗\n"
    "║   🏷️  ПРЕФИКС  (зелёный тег)   ║\n"
    "╚════════════════════════════════╝\n\n"
    "Получи зелёный тег рядом\n"
    "с именем в группе! 🟢\n\n"
    "Выберите длительность:"
)

def prefix_kb() -> InlineKeyboardMarkup:
    rows = [
        [B(f"{p['label']}  →  {p['stars']} ⭐", f"bp:{k}")]
        for k, p in PREFIX_PLANS.items()
    ]
    rows.append([B("🔙 Назад", "shop")])
    return InlineKeyboardMarkup(rows)

MUTE_CARD = (
    "╔════════════════════════════════╗\n"
    "║   🔇 МУТ  (замутить участника)  ║\n"
    "╚════════════════════════════════╝\n\n"
    "Замутьте участника на время! 😶\n\n"
    "Выберите длительность:"
)

def mute_kb() -> InlineKeyboardMarkup:
    rows = [
        [B(f"{p['label']}  →  {p['stars']} ⭐", f"bm:{k}")]
        for k, p in MUTE_PLANS.items()
    ]
    rows.append([B("🔙 Назад", "shop")])
    return InlineKeyboardMarkup(rows)

UNMUTE_CARD = (
    "╔════════════════════════════════╗\n"
    "║   🔊 СНЯТИЕ МУТА (размутить)   ║\n"
    "╚════════════════════════════════╝\n\n"
    "Освободите пользователя! 💚\n\n"
    f"Цена: {UNMUTE_STARS} ⭐"
)

def unmute_kb() -> InlineKeyboardMarkup:
    return KB(
        [B(f"🔊 Купить  —  {UNMUTE_STARS} ⭐", "buy_unmute_step1")],
        [B("🔙 Назад", "shop")],
    )


# ═══════════════════════════════════════════════════════
#                 📩 COMMAND HANDLERS
# ═══════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    
    upsert_user(user.id, user.username or "", user.first_name)

    # In a group — register and check
    if chat.type in ("group", "supergroup"):
        cfg = get_config()
        cfg["group_id"] = chat.id
        cfg["group_name"] = chat.title
        save_config(cfg)
        
        is_ok, msg = await check_bot_permissions(ctx.bot, chat.id)
        if is_ok:
            await update.message.reply_text(
                f"✅ Группа <b>{chat.title}</b> зарегистрирована!\n\n"
                f"{msg}\n\n"
                "Напишите /start в личные сообщения",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                f"⚠️ Группа зарегистрирована, но есть проблемы:\n\n{msg}",
                parse_mode=ParseMode.HTML,
            )
        return

    # In PM — show main menu
    try:
        await update.message.delete()
    except:
        pass
    
    await update.message.reply_text(
        MAIN_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb(),
    )


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Check bot permissions."""
    chat = update.effective_chat
    
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Используйте в группе")
        return
    
    is_ok, msg = await check_bot_permissions(ctx.bot, chat.id)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════════════════
#               🎛️ CALLBACK HANDLERS
# ═══════════════════════════════════════════════════════

async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    d = q.data
    user = update.effective_user
    
    await q.answer()
    upsert_user(user.id, user.username or "", user.first_name)

    cfg = get_config()
    group_id = cfg.get("group_id")

    # ── Navigation ────────────────────────────────────
    if d == "main":
        await safe_edit(q, MAIN_TEXT, main_kb())

    elif d == "shop":
        await safe_edit(q, SHOP_TEXT, shop_kb())

    elif d == "p_card":
        await safe_edit(q, PREFIX_CARD, prefix_kb())

    elif d == "m_card":
        await safe_edit(q, MUTE_CARD, mute_kb())

    elif d == "u_card":
        await safe_edit(q, UNMUTE_CARD, unmute_kb())

    # ── Profile ───────────────────────────────────────
    elif d == "profile":
        u = get_user(user.id)
        name = user.first_name
        at = f"@{user.username}" if user.username else "—"
        spent = u.get("stars_spent", 0)
        buys = u.get("purchases", 0)

        pfx_line = ""
        pfx = get_prefixes().get(str(user.id))
        if pfx and pfx.get("expires_at"):
            exp = datetime.fromisoformat(pfx["expires_at"])
            if exp > datetime.utcnow():
                pfx_line = f"\n🏷️ Префикс до {exp.strftime('%d.%m %H:%M')}"

        txt = (
            "╔════════════════════════════════╗\n"
            "║        👤 МОЙ ПРОФИЛЬ         ║\n"
            "╚════════════════════════════════╝\n\n"
            f"Имя: {name}\n"
            f"Username: {at}\n"
            f"ID: {user.id}\n\n"
            f"⭐ Stars: {spent}\n"
            f"🛍️ Покупок: {buys}"
            f"{pfx_line}"
        )
        
        await safe_edit(q, txt, KB(
            [B("🛒 В магазин", "shop")],
            [B("🔙 Главное", "main")],
        ))

    # ── Buy Prefix ──────────────────────────────────
    elif d.startswith("bp:"):
        key = d[3:]
        plan = PREFIX_PLANS[key]
        
        if not group_id:
            await q.answer("Группа не зарегистрирована", show_alert=True)
            return
        
        is_ok, _ = await check_bot_permissions(ctx.bot, group_id)
        if not is_ok:
            await q.answer("Бот не имеет прав", show_alert=True)
            return
        
        save_pending_purchase(user.id, {
            "type": "prefix",
            "plan_key": key,
            "stars": plan["stars"],
            "label": plan["label"],
            "minutes": plan["minutes"],
        })
        
        try:
            await ctx.bot.send_invoice(
                chat_id=user.id,
                title=f"🏷️ Префикс — {plan['label']}",
                description=f"Зелёный тег на {plan['label']}",
                payload=f"prefix:{key}:{user.id}",
                currency="XTR",
                prices=[LabeledPrice(f"Префикс {plan['label']}", plan["stars"])],
            )
        except Exception as e:
            logger.error(f"Invoice error: {e}")
            await q.answer(f"Ошибка: {str(e)}", show_alert=True)

    # ── Buy Mute: get target ─────────────────────────
    elif d.startswith("bm:"):
        key = d[3:]
        plan = MUTE_PLANS[key]
        
        if not group_id:
            await q.answer("Группа не зарегистрирована", show_alert=True)
            return
        
        ctx.user_data["pending_mute_plan"] = key
        ctx.user_data["awaiting_mute_target"] = True
        
        await safe_edit(q, 
            f"🔇 МУТ на {plan['label']} ({plan['stars']} ⭐)\n\n"
            "Введите @username или ID пользователя:",
            KB([B("❌ Отмена", "m_card")])
        )

    # ── Buy Unmute: get target ───────────────────────
    elif d == "buy_unmute_step1":
        if not group_id:
            await q.answer("Группа не зарегистрирована", show_alert=True)
            return
        
        ctx.user_data["awaiting_unmute_target"] = True
        
        await safe_edit(q,
            f"🔊 СНЯТИЕ МУТА ({UNMUTE_STARS} ⭐)\n\n"
            "Введите @username или ID пользователя:",
            KB([B("❌ Отмена", "u_card")])
        )


# ═══════════════════════════════════════════════════════
#              💬 TEXT INPUT HANDLER
# ═══════════════════════════════════════════════════════

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    text = update.message.text.strip()
    cfg = get_config()
    group_id = cfg.get("group_id")

    if not group_id:
        await update.message.reply_text("Группа не зарегистрирована")
        return

    # ── Mute Target ────────────────────────────────
    if ctx.user_data.get("awaiting_mute_target"):
        ctx.user_data.pop("awaiting_mute_target", None)
        key = ctx.user_data.pop("pending_mute_plan", None)
        
        if not key:
            await update.message.reply_text("Ошибка. Попробуйте снова.")
            return
        
        plan = MUTE_PLANS[key]
        
        # Resolve user
        target_user = await resolve_user(ctx.bot, group_id, text)
        if not target_user:
            await update.message.reply_text(
                f"❌ Пользователь <b>{text}</b> не найден",
                parse_mode=ParseMode.HTML,
            )
            return
        
        at = f"@{user.username}" if user.username else user.first_name
        tname = f"@{target_user.username}" if target_user.username else target_user.first_name
        
        suggestion = (
            f"✅ Найден!\n\n"
            f"👤 {tname} (ID: {target_user.id})\n"
            f"🔇 Мут: {plan['label']}\n"
            f"💰 Цена: {plan['stars']} ⭐\n\n"
            "Продолжить?"
        )
        
        save_pending_purchase(user.id, {
            "type": "mute",
            "plan_key": key,
            "stars": plan["stars"],
            "label": plan["label"],
            "minutes": plan["minutes"],
            "target_user_id": target_user.id,
            "target_username": tname,
        })
        
        await update.message.reply_text(
            suggestion,
            parse_mode=ParseMode.HTML,
            reply_markup=KB(
                [B("💳 Оплатить", "confirm_mute")],
                [B("❌ Отмена", "m_card")],
            ),
        )

    # ── Unmute Target ──────────────────────────────
    elif ctx.user_data.get("awaiting_unmute_target"):
        ctx.user_data.pop("awaiting_unmute_target", None)
        
        target_user = await resolve_user(ctx.bot, group_id, text)
        if not target_user:
            await update.message.reply_text(
                f"❌ Пользователь <b>{text}</b> не найден",
                parse_mode=ParseMode.HTML,
            )
            return
        
        at = f"@{user.username}" if user.username else user.first_name
        tname = f"@{target_user.username}" if target_user.username else target_user.first_name
        
        suggestion = (
            f"✅ Найден!\n\n"
            f"👤 {tname} (ID: {target_user.id})\n"
            f"🔊 Снять мут\n"
            f"💰 Цена: {UNMUTE_STARS} ⭐\n\n"
            "Продолжить?"
        )
        
        save_pending_purchase(user.id, {
            "type": "unmute",
            "stars": UNMUTE_STARS,
            "target_user_id": target_user.id,
            "target_username": tname,
        })
        
        await update.message.reply_text(
            suggestion,
            parse_mode=ParseMode.HTML,
            reply_markup=KB(
                [B("💳 Оплатить", "confirm_unmute")],
                [B("❌ Отмена", "u_card")],
            ),
        )


# ═══════════════════════════════════════════════════════
#        🎛️ CALLBACK - CONFIRM PURCHASES
# ═══════════════════════════════════════════════════════

async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    d = q.data
    user = update.effective_user
    
    await q.answer()
    
    pending = get_pending()
    uid_str = str(user.id)
    
    if uid_str not in pending:
        await q.answer("Заказ не найден", show_alert=True)
        return

    p = pending[uid_str]
    
    if d == "confirm_mute":
        plan = MUTE_PLANS[p.get("plan_key", "10m")]
        try:
            await ctx.bot.send_invoice(
                chat_id=user.id,
                title=f"🔇 Мут — {plan['label']}",
                description=f"Замутить {p.get('target_username')}",
                payload=f"mute:{p['plan_key']}:{user.id}",
                currency="XTR",
                prices=[LabeledPrice(f"Мут {plan['label']}", plan["stars"])],
            )
        except Exception as e:
            logger.error(f"Invoice: {e}")
            await q.answer(f"Ошибка: {str(e)}", show_alert=True)

    elif d == "confirm_unmute":
        try:
            await ctx.bot.send_invoice(
                chat_id=user.id,
                title="🔊 Снятие мута",
                description=f"Размутить {p.get('target_username')}",
                payload=f"unmute:{user.id}",
                currency="XTR",
                prices=[LabeledPrice("Снятие мута", UNMUTE_STARS)],
            )
        except Exception as e:
            logger.error(f"Invoice: {e}")
            await q.answer(f"Ошибка: {str(e)}", show_alert=True)


# ═══════════════════════════════════════════════════════
#               💳 PAYMENT HANDLERS
# ═══════════════════════════════════════════════════════

async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.pre_checkout_query.answer(ok=True)


async def payment_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    payment = update.message.successful_payment
    stars = payment.total_amount

    record_purchase(user.id, stars)

    pending = pop_pending_purchase(user.id)
    cfg = get_config()
    group_id = cfg.get("group_id")

    if not pending:
        await update.message.reply_text("Заказ не найден")
        return

    ptype = pending.get("type")
    
    if ptype == "prefix":
        await do_prefix(update, ctx, user, pending, group_id)
    elif ptype == "mute":
        await do_mute(update, ctx, user, pending, group_id)
    elif ptype == "unmute":
        await do_unmute(update, ctx, user, pending, group_id)


# ═══════════════════════════════════════════════════════
#              🛠️ HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════

async def resolve_user(bot, group_id: int, identifier: str):
    """Resolve @username or ID to User."""
    identifier = identifier.strip()
    try:
        if identifier.startswith("@"):
            uid = identifier
        else:
            uid = int(identifier)
        
        m = await bot.get_chat_member(group_id, uid)
        return m.user
    except Exception as e:
        logger.warning(f"Resolve error: {e}")
        return None


async def notify_admins(bot, group_id: int, text: str) -> None:
    try:
        admins = await bot.get_chat_administrators(group_id)
        for a in admins:
            if not a.user.is_bot:
                try:
                    await bot.send_message(a.user.id, text, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Notify admins: {e}")


async def bot_link(bot) -> str:
    me = await bot.get_me()
    return f"https://t.me/{me.username}"


FULL_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
)

MUTE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
)


# ═══════════════════════════════════════════════════════
#            ✨ ACTIONS: PREFIX / MUTE / UNMUTE
# ═══════════════════════════════════════════════════════

async def do_prefix(update, ctx, user, pending, group_id) -> None:
    key = pending["plan_key"]
    plan = PREFIX_PLANS[key]
    link = await bot_link(ctx.bot)

    if not group_id:
        await update.message.reply_text("Группа не зарегистрирована")
        return

    try:
        await ctx.bot.promote_chat_member(
            chat_id=group_id,
            user_id=user.id,
            is_anonymous=False,
            can_manage_chat=False,
            can_change_info=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        
        await ctx.bot.set_chat_administrator_custom_title(
            chat_id=group_id,
            user_id=user.id,
            custom_title="🟢 VIP",
        )
    except TelegramError as e:
        logger.error(f"Prefix error: {e}")
        await update.message.reply_text(f"Ошибка: {e}")
        return

    expires_at = None
    if plan["minutes"]:
        expires_at = (datetime.utcnow() + timedelta(minutes=plan["minutes"])).isoformat()

    pfx = get_prefixes()
    pfx[str(user.id)] = {
        "group_id": group_id,
        "expires_at": expires_at,
    }
    save_prefixes(pfx)

    if plan["minutes"]:
        ctx.job_queue.run_once(
            job_remove_prefix,
            plan["minutes"] * 60,
            data={"user_id": user.id, "group_id": group_id},
            name=f"pfx_{user.id}",
        )

    at = f"@{user.username}" if user.username else user.first_name

    await update.message.reply_text(
        f"✅ <b>УСПЕШНО!</b>\n\n"
        f"🏷️ Префикс активирован!\n"
        f"⏱️ {plan['label']}\n"
        f"⭐ {plan['stars']} Stars",
        parse_mode=ParseMode.HTML,
    )

    await notify_admins(
        ctx.bot, group_id,
        f"🛍️ <b>ПОКУПКА!</b>\n\n"
        f"👤 {at} (ID: {user.id})\n"
        f"📦 Префикс на {plan['label']}\n"
        f"⭐ {plan['stars']} Stars",
    )

    await ctx.bot.send_message(
        group_id,
        f"🏷️ {at} купил <b>зелёный префикс</b> на {plan['label']}! 🟢",
        parse_mode=ParseMode.HTML,
        reply_markup=KB([B("🛒 Я тоже!", url=link)]),
    )


async def job_remove_prefix(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = ctx.job.data
    uid = d["user_id"]
    gid = d["group_id"]

    try:
        await ctx.bot.promote_chat_member(
            chat_id=gid,
            user_id=uid,
            is_anonymous=False,
            can_manage_chat=False,
            can_change_info=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
    except Exception as e:
        logger.error(f"Remove prefix: {e}")

    pfx = get_prefixes()
    pfx.pop(str(uid), None)
    save_prefixes(pfx)

    try:
        await ctx.bot.send_message(
            uid,
            "⏰ Ваш префикс истёк",
            reply_markup=KB([B("🛒 Купить ещё", "shop")]),
        )
    except:
        pass


async def do_mute(update, ctx, user, pending, group_id) -> None:
    target_id = pending.get("target_user_id")
    target_name = pending.get("target_username", "Unknown")
    plan = MUTE_PLANS[pending["plan_key"]]
    link = await bot_link(ctx.bot)

    if not target_id:
        await update.message.reply_text("Пользователь не найден")
        return

    until = None
    if plan["minutes"]:
        until = datetime.utcnow() + timedelta(minutes=plan["minutes"])

    try:
        await ctx.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=target_id,
            permissions=MUTE_PERMS,
            until_date=until,
        )
    except TelegramError as e:
        logger.error(f"Mute error: {e}")
        await update.message.reply_text(f"Ошибка: {e}")
        return

    mutes = get_mutes()
    mutes[str(target_id)] = {
        "group_id": group_id,
        "expires_at": until.isoformat() if until else None,
    }
    save_mutes(mutes)

    if plan["minutes"]:
        ctx.job_queue.run_once(
            job_auto_unmute,
            plan["minutes"] * 60,
            data={"user_id": target_id, "group_id": group_id},
            name=f"mute_{target_id}",
        )

    at = f"@{user.username}" if user.username else user.first_name

    await update.message.reply_text(
        f"✅ <b>УСПЕШНО!</b>\n\n"
        f"🔇 Мут выдан\n"
        f"👤 {target_name}\n"
        f"⏱️ {plan['label']}\n"
        f"⭐ {plan['stars']} Stars",
        parse_mode=ParseMode.HTML,
    )

    await notify_admins(
        ctx.bot, group_id,
        f"🛍️ <b>ПОКУПКА!</b>\n\n"
        f"👤 {at}\n"
        f"📦 Мут {target_name} на {plan['label']}\n"
        f"⭐ {plan['stars']} Stars",
    )

    await ctx.bot.send_message(
        group_id,
        f"🔇 {at} замутил {target_name} на <b>{plan['label']}</b>!",
        parse_mode=ParseMode.HTML,
        reply_markup=KB([B("🛒 Я тоже!", url=link)]),
    )


async def job_auto_unmute(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = ctx.job.data
    uid = d["user_id"]
    gid = d["group_id"]

    try:
        await ctx.bot.restrict_chat_member(
            chat_id=gid,
            user_id=uid,
            permissions=FULL_PERMS,
        )
        mutes = get_mutes()
        mutes.pop(str(uid), None)
        save_mutes(mutes)
    except Exception as e:
        logger.error(f"Auto-unmute: {e}")


async def do_unmute(update, ctx, user, pending, group_id) -> None:
    target_id = pending.get("target_user_id")
    target_name = pending.get("target_username", "Unknown")
    link = await bot_link(ctx.bot)

    if not target_id:
        await update.message.reply_text("Пользователь не найден")
        return

    try:
        await ctx.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=target_id,
            permissions=FULL_PERMS,
        )
    except TelegramError as e:
        logger.error(f"Unmute error: {e}")
        await update.message.reply_text(f"Ошибка: {e}")
        return

    mutes = get_mutes()
    mutes.pop(str(target_id), None)
    save_mutes(mutes)

    for j in ctx.job_queue.get_jobs_by_name(f"mute_{target_id}"):
        j.schedule_removal()

    at = f"@{user.username}" if user.username else user.first_name

    await update.message.reply_text(
        f"✅ <b>УСПЕШНО!</b>\n\n"
        f"🔊 Мут снят\n"
        f"👤 {target_name}\n"
        f"⭐ {UNMUTE_STARS} Stars",
        parse_mode=ParseMode.HTML,
    )

    await notify_admins(
        ctx.bot, group_id,
        f"🛍️ <b>ПОКУПКА!</b>\n\n"
        f"👤 {at}\n"
        f"📦 Снял мут с {target_name}\n"
        f"⭐ {UNMUTE_STARS} Stars",
    )

    await ctx.bot.send_message(
        group_id,
        f"🔊 {at} снял мут с {target_name}!",
        parse_mode=ParseMode.HTML,
        reply_markup=KB([B("🛒 Я тоже!", url=link)]),
    )


# ═══════════════════════════════════════════════════════
#          ♻️ JOB RESTORATION
# ═══════════════════════════════════════════════════════

async def restore_jobs(app: Application) -> None:
    """Restore scheduled jobs after restart."""
    now = datetime.utcnow()
    logger.info("Restoring jobs...")

    pfx = get_prefixes()
    to_del = []
    for uid, data in pfx.items():
        if not data.get("expires_at"):
            continue
        exp = datetime.fromisoformat(data["expires_at"])
        if exp > now:
            app.job_queue.run_once(
                job_remove_prefix,
                (exp - now).total_seconds(),
                data={"user_id": int(uid), "group_id": data["group_id"]},
                name=f"pfx_{uid}",
            )
        else:
            to_del.append(uid)
    for uid in to_del:
        del pfx[uid]
    save_prefixes(pfx)

    mutes = get_mutes()
    to_del = []
    for uid, data in mutes.items():
        if not data.get("expires_at"):
            continue
        exp = datetime.fromisoformat(data["expires_at"])
        if exp > now:
            app.job_queue.run_once(
                job_auto_unmute,
                (exp - now).total_seconds(),
                data={"user_id": int(uid), "group_id": data["group_id"]},
                name=f"mute_{uid}",
            )
        else:
            to_del.append(uid)
    for uid in to_del:
        del mutes[uid]
    save_mutes(mutes)

    logger.info("Jobs restored")


# ═══════════════════════════════════════════════════════
#                    🚀 MAIN
# ═══════════════════════════════════════════════════════

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(restore_jobs)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern="^(confirm_mute|confirm_unmute)$"))
    app.add_handler(CallbackQueryHandler(cb_handler))

    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_done))
    
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        text_handler,
    ))

    logger.info("🚀 BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
