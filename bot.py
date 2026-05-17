#!/usr/bin/env python3
"""
╔══════════════════════════════════════════╗
║   🌟 Telegram Shop Bot — Stars Edition  ║
║   Prefix • Mute • Unmute  |  v1.0       ║
╚══════════════════════════════════════════╝
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
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
#                   🔧 CONFIGURATION
# ═══════════════════════════════════════════════════════

BOT_TOKEN = "8057185585:AAF_LJKPk1OW3U3x7OqOnl0TO2Dux_2mDM0"   # ← paste your token here

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
    "10m":  {"stars": 1,    "minutes": 10,    "label": "10 минут",  "emoji": "⚡"},
    "1h":   {"stars": 100,   "minutes": 60,    "label": "1 час",     "emoji": "🕐"},
    "5h":   {"stars": 150,   "minutes": 300,   "label": "5 часов",   "emoji": "🕔"},
    "10h":  {"stars": 200,   "minutes": 600,   "label": "10 часов",  "emoji": "🕙"},
    "24h":  {"stars": 300,   "minutes": 1440,  "label": "24 часа",   "emoji": "📅"},
    "inf":  {"stars": 400,   "minutes": None,  "label": "Навсегда",  "emoji": "♾️"},
}

MUTE_PLANS = {
    "10m":  {"stars": 1,    "minutes": 10,    "label": "10 минут",  "emoji": "⚡"},
    "1h":   {"stars": 100,   "minutes": 60,    "label": "1 час",     "emoji": "🕐"},
    "5h":   {"stars": 150,   "minutes": 300,   "label": "5 часов",   "emoji": "🕔"},
    "10h":  {"stars": 200,   "minutes": 600,   "label": "10 часов",  "emoji": "🕙"},
    "24h":  {"stars": 300,   "minutes": 1440,  "label": "24 часа",   "emoji": "📅"},
    "inf":  {"stars": 1000,  "minutes": None,  "label": "Навсегда",  "emoji": "♾️"},
}

UNMUTE_STARS = 1


# ═══════════════════════════════════════════════════════
#                  🗄️ DATABASE LAYER
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
    uid   = str(user_id)
    if uid not in users:
        users[uid] = {
            "username": username, "first_name": first_name,
            "stars_spent": 0, "purchases": 0,
            "joined": datetime.utcnow().isoformat(),
        }
    else:
        users[uid]["username"]   = username
        users[uid]["first_name"] = first_name
    save_users(users)

def get_user(user_id: int) -> dict:
    return get_users().get(str(user_id), {})

def record_purchase(user_id: int, stars: int) -> None:
    users = get_users()
    uid   = str(user_id)
    if uid in users:
        users[uid]["stars_spent"] = users[uid].get("stars_spent", 0) + stars
        users[uid]["purchases"]   = users[uid].get("purchases", 0) + 1
        save_users(users)

def save_pending_purchase(user_id: int, data: dict) -> None:
    pending = get_pending()
    pending[str(user_id)] = data
    save_pending(pending)

def pop_pending_purchase(user_id: int) -> dict:
    pending = get_pending()
    data    = pending.pop(str(user_id), {})
    save_pending(pending)
    return data


# ═══════════════════════════════════════════════════════
#              🎨 UI TEXTS & KEYBOARDS
# ═══════════════════════════════════════════════════════

def B(text: str, data: str = None, url: str = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data, url=url)

def KB(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

BACK_MAIN = [B("🔙  Главное меню", "main")]
BACK_SHOP = [B("🔙  В магазин",    "shop")]

# ── Main Menu ──────────────────────────────────────────

MAIN_TEXT = (
    "╔══════════════════════════════╗\n"
    "║  🌟  <b>ПАНЕЛЬ УПРАВЛЕНИЯ</b>  🌟  ║\n"
    "╚══════════════════════════════╝\n\n"
    "👋 Добро пожаловать в наш бот!\n\n"
    "🎮 Выберите раздел ниже:\n\n"
    "   👤 <b>Профиль</b> — ваша статистика\n"
    "   🛒 <b>Магазин</b> — купить привилегии\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "💫 <i>Оплата через Telegram Stars</i>"
)

def main_kb() -> InlineKeyboardMarkup:
    return KB(
        [B("👤  Профиль", "profile"),  B("🛒  Магазин", "shop")],
    )

# ── Shop ───────────────────────────────────────────────

SHOP_TEXT = (
    "╔══════════════════════════════╗\n"
    "║         🛒  <b>МАГАЗИН</b>         ║\n"
    "╚══════════════════════════════╝\n\n"
    "🏷️  <b>Префикс</b>\n"
    "    Зелёный тег рядом с именем\n"
    "    📦 от <b>50 ⭐</b> до <b>400 ⭐</b>\n\n"
    "🔇  <b>Мут</b>\n"
    "    Замутить участника группы\n"
    "    📦 от <b>50 ⭐</b> до <b>1 000 ⭐</b>\n\n"
    "🔊  <b>Снятие мута</b>\n"
    "    Размутить участника\n"
    "    📦 <b>70 ⭐</b> фиксированная цена\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👇 <i>Выберите товар:</i>"
)

def shop_kb() -> InlineKeyboardMarkup:
    return KB(
        [B("🏷️  Префикс",      "p_card")],
        [B("🔇  Мут",           "m_card")],
        [B("🔊  Снятие мута",   "u_card")],
        BACK_MAIN,
    )

# ── Prefix Card ────────────────────────────────────────

PREFIX_CARD = (
    "╔══════════════════════════════╗\n"
    "║   🏷️  <b>ПРЕФИКС В ГРУППЕ</b>   🟢  ║\n"
    "╚══════════════════════════════╝\n\n"
    "✨ Получите эксклюзивный <b>зелёный\n"
    "тег</b> рядом с вашим именем!\n\n"
    "🟢 Тег видят все участники группы\n"
    "🎯 Выдаётся мгновенно после оплаты\n"
    "⏰ Автоматически снимается по истечении\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⭐  <b>Выберите длительность:</b>"
)

def prefix_kb() -> InlineKeyboardMarkup:
    rows = [
        [B(f"{p['emoji']}  {p['label']:<12}  —  {p['stars']:>4} ⭐", f"bp:{k}")]
        for k, p in PREFIX_PLANS.items()
    ]
    rows.append(BACK_SHOP)
    return InlineKeyboardMarkup(rows)

# ── Mute Card ──────────────────────────────────────────

MUTE_CARD = (
    "╔══════════════════════════════╗\n"
    "║   🔇  <b>ЗАМУТИТЬ УЧАСТНИКА</b>    ║\n"
    "╚══════════════════════════════╝\n\n"
    "😶 Замутите любого участника\n"
    "группы на нужное время!\n\n"
    "🔒 Пользователь не сможет писать\n"
    "🔔 Мут снимается автоматически\n"
    "⚡ Действует мгновенно\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⭐  <b>Выберите длительность мута:</b>"
)

def mute_kb() -> InlineKeyboardMarkup:
    rows = [
        [B(f"{p['emoji']}  {p['label']:<12}  —  {p['stars']:>4} ⭐", f"bm:{k}")]
        for k, p in MUTE_PLANS.items()
    ]
    rows.append(BACK_SHOP)
    return InlineKeyboardMarkup(rows)

# ── Unmute Card ────────────────────────────────────────

UNMUTE_CARD = (
    "╔══════════════════════════════╗\n"
    "║     🔊  <b>СНЯТИЕ МУТА</b>         ║\n"
    "╚══════════════════════════════╝\n\n"
    "💚 Освободите пользователя от\n"
    "ограничений в один клик!\n\n"
    "🎯 Укажите @username или ID\n"
    "🚀 Работает мгновенно\n"
    "✅ Восстанавливает все права\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    f"💰 Стоимость: <b>{UNMUTE_STARS} ⭐ Stars</b>\n\n"
    "👇 <i>Нажмите кнопку для покупки:</i>"
)

def unmute_kb() -> InlineKeyboardMarkup:
    return KB(
        [B(f"🔊  Купить снятие мута  —  {UNMUTE_STARS} ⭐", "bu")],
        BACK_SHOP,
    )


# ═══════════════════════════════════════════════════════
#                 📩 COMMAND HANDLERS
# ═══════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    upsert_user(user.id, user.username or "", user.first_name)

    # In a group — register it
    if chat.type in ("group", "supergroup"):
        cfg = get_config()
        cfg["group_id"] = chat.id
        save_config(cfg)
        await update.message.reply_text(
            "✅ <b>Группа успешно зарегистрирована!</b>\n\n"
            "Теперь напишите мне в <b>личные сообщения</b> /start\n"
            "чтобы открыть панель управления.",
            parse_mode=ParseMode.HTML,
        )
        return

    # In PM — show main panel
    msg = await update.message.reply_text(
        MAIN_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb(),
    )
    ctx.user_data["mid"] = msg.message_id


async def cmd_setgroup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin shortcut: /setgroup in the target chat."""
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Используйте эту команду в группе.")
        return
    member = await ctx.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ Только администраторы могут регистрировать группу.")
        return
    cfg = get_config()
    cfg["group_id"] = chat.id
    save_config(cfg)
    await update.message.reply_text(
        f"✅ Группа <b>{chat.title}</b> зарегистрирована!",
        parse_mode=ParseMode.HTML,
    )


# ═══════════════════════════════════════════════════════
#               🎛️ CALLBACK QUERY HANDLER
# ═══════════════════════════════════════════════════════

async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    await q.answer()
    d    = q.data
    user = update.effective_user
    upsert_user(user.id, user.username or "", user.first_name)

    async def edit(text: str, markup: InlineKeyboardMarkup) -> None:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

    # ── Navigation ────────────────────────────────────
    if d == "main":
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("pending_mute_plan", None)
        await edit(MAIN_TEXT, main_kb())

    elif d == "shop":
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("pending_mute_plan", None)
        await edit(SHOP_TEXT, shop_kb())

    elif d == "p_card":
        ctx.user_data.pop("awaiting", None)
        await edit(PREFIX_CARD, prefix_kb())

    elif d == "m_card":
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("pending_mute_plan", None)
        await edit(MUTE_CARD, mute_kb())

    elif d == "u_card":
        ctx.user_data.pop("awaiting", None)
        await edit(UNMUTE_CARD, unmute_kb())

    # ── Profile ───────────────────────────────────────
    elif d == "profile":
        u     = get_user(user.id)
        name  = user.first_name
        at    = f"@{user.username}" if user.username else "—"
        spent = u.get("stars_spent", 0)
        buys  = u.get("purchases",   0)

        pfx_line = ""
        pfx = get_prefixes().get(str(user.id))
        if pfx:
            if pfx.get("expires_at"):
                exp = datetime.fromisoformat(pfx["expires_at"])
                if exp > datetime.utcnow():
                    pfx_line = f"\n🏷️ <b>Префикс:</b> активен до {exp.strftime('%d.%m %H:%M')} UTC"
                else:
                    pfx_line = "\n🏷️ <b>Префикс:</b> срок истёк"
            else:
                pfx_line = "\n🏷️ <b>Префикс:</b> ♾️ навсегда"

        mute_line = ""
        mutes = get_mutes()
        if str(user.id) in mutes:
            m = mutes[str(user.id)]
            if m.get("expires_at"):
                exp = datetime.fromisoformat(m["expires_at"])
                if exp > datetime.utcnow():
                    mute_line = f"\n🔇 <b>Мут:</b> до {exp.strftime('%d.%m %H:%M')} UTC"

        txt = (
            "╔══════════════════════════════╗\n"
            "║        👤  <b>МОЙ ПРОФИЛЬ</b>       ║\n"
            "╚══════════════════════════════╝\n\n"
            f"🙋 <b>Имя:</b> {name}\n"
            f"📎 <b>Username:</b> {at}\n"
            f"🆔 <b>ID:</b> <code>{user.id}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⭐ <b>Потрачено Stars:</b> {spent}\n"
            f"🛍️ <b>Всего покупок:</b> {buys}"
            f"{pfx_line}{mute_line}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💫 <i>Покупайте привилегии в магазине!</i>"
        )
        await edit(txt, KB(
            [B("🛒  Перейти в магазин", "shop")],
            BACK_MAIN,
        ))

    # ── Buy Prefix: send invoice ──────────────────────
    elif d.startswith("bp:"):
        key  = d[3:]
        plan = PREFIX_PLANS[key]
        save_pending_purchase(user.id, {
            "type": "prefix", "plan_key": key,
            "stars": plan["stars"], "label": plan["label"], "minutes": plan["minutes"],
        })
        await ctx.bot.send_invoice(
            chat_id=user.id,
            title=f"🏷️ Префикс — {plan['label']}",
            description=(
                f"Зелёный тег в группе на {plan['label']}. "
                "Выдаётся мгновенно после оплаты."
            ),
            payload=f"prefix:{key}",
            currency="XTR",
            prices=[LabeledPrice(f"Префикс — {plan['label']}", plan["stars"])],
        )

    # ── Buy Mute step 1: select plan ─────────────────
    elif d.startswith("bm:"):
        key  = d[3:]
        plan = MUTE_PLANS[key]
        ctx.user_data["pending_mute_plan"] = key
        ctx.user_data["awaiting"]          = "mute_target"
        await edit(
            "╔══════════════════════════════╗\n"
            f"║  🔇  <b>МУТ — {plan['label']:<19}</b>║\n"
            "╚══════════════════════════════╝\n\n"
            f"💰 Стоимость: <b>{plan['stars']} ⭐ Stars</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>Введите @username или ID</b>\n"
            "пользователя, которого хотите\n"
            "замутить:\n\n"
            "<i>Просто отправьте следующим\nсообщением в этот чат 👇</i>",
            KB([B("❌  Отмена", "m_card")]),
        )

    # ── Buy Unmute step 1: ask target ────────────────
    elif d == "bu":
        ctx.user_data["awaiting"] = "unmute_target"
        await edit(
            "╔══════════════════════════════╗\n"
            "║     🔊  <b>СНЯТИЕ МУТА</b>          ║\n"
            "╚══════════════════════════════╝\n\n"
            f"💰 Стоимость: <b>{UNMUTE_STARS} ⭐ Stars</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>Введите @username или ID</b>\n"
            "пользователя, которого хотите\n"
            "размутить:\n\n"
            "<i>Просто отправьте следующим\nсообщением в этот чат 👇</i>",
            KB([B("❌  Отмена", "u_card")]),
        )


# ═══════════════════════════════════════════════════════
#              💬 TEXT INPUT HANDLER
# ═══════════════════════════════════════════════════════

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return

    user     = update.effective_user
    text     = update.message.text.strip()
    awaiting = ctx.user_data.get("awaiting")

    if not awaiting:
        return  # Not waiting for input — ignore

    if awaiting == "mute_target":
        ctx.user_data.pop("awaiting", None)
        key = ctx.user_data.pop("pending_mute_plan", None)
        if not key:
            await update.message.reply_text("❌ Ошибка состояния. Начните сначала.")
            return
        plan = MUTE_PLANS[key]
        save_pending_purchase(user.id, {
            "type": "mute", "plan_key": key,
            "stars": plan["stars"], "label": plan["label"],
            "minutes": plan["minutes"], "target": text,
        })
        await ctx.bot.send_invoice(
            chat_id=user.id,
            title=f"🔇 Мут — {plan['label']}",
            description=f"Замутить {text} на {plan['label']}. Применяется мгновенно.",
            payload=f"mute:{key}",
            currency="XTR",
            prices=[LabeledPrice(f"Мут — {plan['label']}", plan["stars"])],
        )

    elif awaiting == "unmute_target":
        ctx.user_data.pop("awaiting", None)
        save_pending_purchase(user.id, {
            "type": "unmute", "stars": UNMUTE_STARS, "target": text,
        })
        await ctx.bot.send_invoice(
            chat_id=user.id,
            title="🔊 Снятие мута",
            description=f"Снять мут с {text}. Применяется мгновенно.",
            payload="unmute",
            currency="XTR",
            prices=[LabeledPrice("Снятие мута", UNMUTE_STARS)],
        )


# ═══════════════════════════════════════════════════════
#               💳 PAYMENT HANDLERS
# ═══════════════════════════════════════════════════════

async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.pre_checkout_query.answer(ok=True)


async def payment_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    payment = update.message.successful_payment
    stars   = payment.total_amount

    record_purchase(user.id, stars)

    pending  = pop_pending_purchase(user.id)
    cfg      = get_config()
    group_id = cfg.get("group_id")

    if not pending:
        await update.message.reply_text(
            "✅ Оплата получена!\n\n"
            "⚠️ Данные заказа не найдены. Обратитесь к администратору."
        )
        return

    ptype = pending.get("type")
    if ptype == "prefix":
        await do_prefix(update, ctx, user, pending, group_id)
    elif ptype == "mute":
        await do_mute(update, ctx, user, pending, group_id)
    elif ptype == "unmute":
        await do_unmute(update, ctx, user, pending, group_id)


# ═══════════════════════════════════════════════════════
#               🛠️ ACTION EXECUTORS
# ═══════════════════════════════════════════════════════

async def resolve_user(bot, group_id: int, identifier: str):
    """Resolve @username or numeric ID to a Telegram User object."""
    identifier = identifier.strip()
    try:
        uid = int(identifier) if not identifier.startswith("@") else identifier
        m   = await bot.get_chat_member(group_id, uid)
        return m.user
    except Exception as e:
        logger.warning(f"Cannot resolve '{identifier}': {e}")
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
        logger.error(f"notify_admins: {e}")


async def bot_link(bot: any) -> str:
    me = await bot.get_me()
    return f"https://t.me/{me.username}"


# ─── FULL_PERMS: all chat permissions restored ──────────
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

# ─── MUTE_PERMS: no message sending ──────────────────────
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


# ─── PREFIX ──────────────────────────────────────────────

async def do_prefix(update, ctx, user, pending, group_id) -> None:
    key  = pending["plan_key"]
    plan = PREFIX_PLANS[key]
    link = await bot_link(ctx.bot)

    if not group_id:
        await update.message.reply_text(
            "❌ Группа не настроена администратором.\n"
            "Попросите его прислать /start или /setgroup в группе."
        )
        return

    try:
        # Promote to admin (minimal rights) to allow custom title
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
        # Set the green tag title
        await ctx.bot.set_chat_administrator_custom_title(
            chat_id=group_id,
            user_id=user.id,
            custom_title="🟢 VIP",
        )
    except TelegramError as e:
        logger.error(f"do_prefix promote error: {e}")
        await update.message.reply_text(
            f"❌ Не удалось выдать префикс.\n\n"
            f"Причина: <code>{e}</code>\n\n"
            "Убедитесь, что бот — администратор группы с правом назначать администраторов.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Persist to DB
    expires_at = None
    if plan["minutes"]:
        expires_at = (datetime.utcnow() + timedelta(minutes=plan["minutes"])).isoformat()

    pfx = get_prefixes()
    pfx[str(user.id)] = {
        "group_id":  group_id,
        "expires_at": expires_at,
        "plan_key":  key,
    }
    save_prefixes(pfx)

    # Schedule automatic removal
    if plan["minutes"]:
        for j in ctx.job_queue.get_jobs_by_name(f"pfx_{user.id}"):
            j.schedule_removal()
        ctx.job_queue.run_once(
            job_remove_prefix,
            plan["minutes"] * 60,
            data={"user_id": user.id, "group_id": group_id},
            name=f"pfx_{user.id}",
        )

    at    = f"@{user.username}" if user.username else user.first_name
    exp_s = f"до {datetime.fromisoformat(expires_at).strftime('%d.%m %H:%M')} UTC" if expires_at else "навсегда ♾️"

    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  ✅  <b>ПОКУПКА УСПЕШНА!</b>       ║\n"
        "╚══════════════════════════════╝\n\n"
        "🏷️ <b>Префикс активирован!</b>\n\n"
        f"⏱️ Длительность: <b>{plan['label']}</b>\n"
        f"🕐 Активен {exp_s}\n"
        f"⭐ Потрачено: <b>{plan['stars']} Stars</b>\n\n"
        "🟢 Ваш зелёный тег уже виден в группе!",
        parse_mode=ParseMode.HTML,
        reply_markup=KB([B("🛒  Ещё в магазине", "shop")]),
    )

    await notify_admins(
        ctx.bot, group_id,
        "🛍️ <b>Новая покупка в магазине!</b>\n\n"
        f"👤 Покупатель: {at} (<code>{user.id}</code>)\n"
        f"📦 Товар: <b>Префикс</b>\n"
        f"⏱️ Длительность: {plan['label']}\n"
        f"⭐ Стоимость: {plan['stars']} Stars",
    )

    await ctx.bot.send_message(
        group_id,
        f"🏷️ Пользователь {at} приобрёл <b>зелёный префикс</b> на <b>{plan['label']}</b>!",
        parse_mode=ParseMode.HTML,
        reply_markup=KB([B("🛒  Хочу так же!", url=link)]),
    )


async def job_remove_prefix(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d   = ctx.job.data
    uid = d["user_id"]
    gid = d["group_id"]

    try:
        await ctx.bot.promote_chat_member(
            chat_id=gid, user_id=uid,
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
        logger.error(f"job_remove_prefix demote error: {e}")

    pfx = get_prefixes()
    pfx.pop(str(uid), None)
    save_prefixes(pfx)

    try:
        await ctx.bot.send_message(
            uid,
            "⌛ Срок вашего <b>зелёного префикса</b> истёк.\n\n"
            "Хотите продлить? Загляните в магазин!",
            parse_mode=ParseMode.HTML,
            reply_markup=KB([B("🛒  В магазин", "shop")]),
        )
    except Exception:
        pass


# ─── MUTE ────────────────────────────────────────────────

async def do_mute(update, ctx, user, pending, group_id) -> None:
    target_str = pending.get("target", "")
    plan       = MUTE_PLANS[pending["plan_key"]]
    link       = await bot_link(ctx.bot)

    if not group_id:
        await update.message.reply_text("❌ Группа не настроена администратором.")
        return

    target_user = await resolve_user(ctx.bot, group_id, target_str)
    if not target_user:
        await update.message.reply_text(
            f"❌ Пользователь <b>{target_str}</b> не найден в группе.\n\n"
            "Убедитесь, что он является участником группы и правильно указан.",
            parse_mode=ParseMode.HTML,
        )
        return

    until = None
    if plan["minutes"]:
        until = datetime.utcnow() + timedelta(minutes=plan["minutes"])

    try:
        await ctx.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=target_user.id,
            permissions=MUTE_PERMS,
            until_date=until,
        )
    except TelegramError as e:
        logger.error(f"do_mute restrict error: {e}")
        await update.message.reply_text(
            f"❌ Не удалось выдать мут.\n\n"
            f"Причина: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Persist
    mutes = get_mutes()
    mutes[str(target_user.id)] = {
        "group_id":  group_id,
        "expires_at": until.isoformat() if until else None,
        "muted_by":  user.id,
    }
    save_mutes(mutes)

    # Schedule auto-unmute
    if plan["minutes"]:
        for j in ctx.job_queue.get_jobs_by_name(f"mute_{target_user.id}"):
            j.schedule_removal()
        ctx.job_queue.run_once(
            job_auto_unmute,
            plan["minutes"] * 60,
            data={"user_id": target_user.id, "group_id": group_id},
            name=f"mute_{target_user.id}",
        )

    at    = f"@{user.username}"       if user.username        else user.first_name
    tname = f"@{target_user.username}" if target_user.username else target_user.first_name
    exp_s = f"до {until.strftime('%d.%m %H:%M')} UTC" if until else "навсегда ♾️"

    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  ✅  <b>ПОКУПКА УСПЕШНА!</b>       ║\n"
        "╚══════════════════════════════╝\n\n"
        "🔇 <b>Мут успешно выдан!</b>\n\n"
        f"🎯 Пользователь: <b>{tname}</b>\n"
        f"⏱️ Длительность: <b>{plan['label']}</b>\n"
        f"🕐 Активен {exp_s}\n"
        f"⭐ Потрачено: <b>{plan['stars']} Stars</b>",
        parse_mode=ParseMode.HTML,
    )

    await notify_admins(
        ctx.bot, group_id,
        "🛍️ <b>Новая покупка в магазине!</b>\n\n"
        f"👤 Покупатель: {at} (<code>{user.id}</code>)\n"
        f"📦 Товар: <b>Мут</b>\n"
        f"🎯 Цель: {tname} (<code>{target_user.id}</code>)\n"
        f"⏱️ Длительность: {plan['label']}\n"
        f"⭐ Стоимость: {plan['stars']} Stars",
    )

    await ctx.bot.send_message(
        group_id,
        f"🔇 <b>{at}</b> замутил <b>{tname}</b> на <b>{plan['label']}</b>!",
        parse_mode=ParseMode.HTML,
        reply_markup=KB([B("🛒  Сделать так же!", url=link)]),
    )


async def job_auto_unmute(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d   = ctx.job.data
    uid = d["user_id"]
    gid = d["group_id"]

    try:
        await ctx.bot.restrict_chat_member(
            chat_id=gid, user_id=uid,
            permissions=FULL_PERMS,
        )
        mutes = get_mutes()
        mutes.pop(str(uid), None)
        save_mutes(mutes)
        logger.info(f"Auto-unmuted user {uid} in chat {gid}")
    except Exception as e:
        logger.error(f"job_auto_unmute error: {e}")


# ─── UNMUTE ───────────────────────────────────────────────

async def do_unmute(update, ctx, user, pending, group_id) -> None:
    target_str  = pending.get("target", "")
    link        = await bot_link(ctx.bot)

    if not group_id:
        await update.message.reply_text("❌ Группа не настроена администратором.")
        return

    target_user = await resolve_user(ctx.bot, group_id, target_str)
    if not target_user:
        await update.message.reply_text(
            f"❌ Пользователь <b>{target_str}</b> не найден в группе.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        await ctx.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=target_user.id,
            permissions=FULL_PERMS,
        )
    except TelegramError as e:
        logger.error(f"do_unmute error: {e}")
        await update.message.reply_text(
            f"❌ Не удалось снять мут.\n\nПричина: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Clean DB & cancel any pending auto-unmute job
    mutes = get_mutes()
    mutes.pop(str(target_user.id), None)
    save_mutes(mutes)

    for j in ctx.job_queue.get_jobs_by_name(f"mute_{target_user.id}"):
        j.schedule_removal()

    at    = f"@{user.username}"       if user.username        else user.first_name
    tname = f"@{target_user.username}" if target_user.username else target_user.first_name

    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  ✅  <b>ПОКУПКА УСПЕШНА!</b>       ║\n"
        "╚══════════════════════════════╝\n\n"
        "🔊 <b>Мут успешно снят!</b>\n\n"
        f"🎯 Пользователь: <b>{tname}</b>\n"
        f"✅ Все права восстановлены\n"
        f"⭐ Потрачено: <b>{UNMUTE_STARS} Stars</b>",
        parse_mode=ParseMode.HTML,
    )

    await notify_admins(
        ctx.bot, group_id,
        "🛍️ <b>Новая покупка в магазине!</b>\n\n"
        f"👤 Покупатель: {at} (<code>{user.id}</code>)\n"
        f"📦 Товар: <b>Снятие мута</b>\n"
        f"🎯 Цель: {tname} (<code>{target_user.id}</code>)\n"
        f"⭐ Стоимость: {UNMUTE_STARS} Stars",
    )

    await ctx.bot.send_message(
        group_id,
        f"🔊 <b>{at}</b> снял мут с <b>{tname}</b>!",
        parse_mode=ParseMode.HTML,
        reply_markup=KB([B("🛒  Сделать так же!", url=link)]),
    )


# ═══════════════════════════════════════════════════════
#          ♻️  JOB RESTORATION ON STARTUP
# ═══════════════════════════════════════════════════════

async def restore_jobs(app: Application) -> None:
    """Re-schedule any pending prefix/mute expiry jobs after a bot restart."""
    now = datetime.utcnow()
    logger.info("Restoring scheduled jobs...")

    # Prefix jobs
    pfx     = get_prefixes()
    to_del  = []
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
            logger.info(f"  ↩ Restored prefix job for user {uid}")
        else:
            # Already expired while bot was offline — demote silently
            try:
                await app.bot.promote_chat_member(
                    chat_id=data["group_id"], user_id=int(uid),
                    is_anonymous=False, can_manage_chat=False,
                    can_change_info=False, can_delete_messages=False,
                    can_manage_video_chats=False, can_restrict_members=False,
                    can_promote_members=False, can_invite_users=False,
                    can_pin_messages=False,
                )
            except Exception:
                pass
            to_del.append(uid)
            logger.info(f"  ✗ Prefix expired (offline) for user {uid} — demoted")
    for uid in to_del:
        del pfx[uid]
    save_prefixes(pfx)

    # Mute jobs
    mutes  = get_mutes()
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
            logger.info(f"  ↩ Restored mute job for user {uid}")
        else:
            # Already expired while bot was offline — unmute silently
            try:
                await app.bot.restrict_chat_member(
                    chat_id=data["group_id"],
                    user_id=int(uid),
                    permissions=FULL_PERMS,
                )
            except Exception:
                pass
            to_del.append(uid)
            logger.info(f"  ✗ Mute expired (offline) for user {uid} — unmuted")
    for uid in to_del:
        del mutes[uid]
    save_mutes(mutes)

    logger.info("Job restoration complete ✅")


# ═══════════════════════════════════════════════════════
#                    🚀 ENTRY POINT
# ═══════════════════════════════════════════════════════

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(restore_jobs)
        .build()
    )

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_done))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        text_handler,
    ))

    logger.info("🤖 Bot is running — press Ctrl+C to stop")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
