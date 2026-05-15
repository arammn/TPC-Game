import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List

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
# Настройки и база данных
# -------------------------------------------------------------------
CONFIG_FILE = "config.json"
DB_FILE = "db.json"

with open(CONFIG_FILE) as f:
    config = json.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
GROUP_CHAT_ID = config["GROUP_CHAT_ID"]
ADMIN_IDS: List[int] = config.get("ADMIN_IDS", [])

# Инициализация БД
DEFAULT_DB = {
    "prefixes": [],
    "mutes": [],
    "history": [],
}
if not os.path.exists(DB_FILE):
    with open(DB_FILE, "w") as f:
        json.dump(DEFAULT_DB, f)
else:
    with open(DB_FILE) as f:
        db = json.load(f)
    updated = False
    for key, val in DEFAULT_DB.items():
        if key not in db:
            db[key] = val
            updated = True
    if updated:
        with open(DB_FILE, "w") as f:
            json.dump(db, f, indent=2)

def load_db():
    with open(DB_FILE) as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# -------------------------------------------------------------------
# Цены и длительности
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
    "10min": "10 минут",
    "1hour": "1 час",
    "5hours": "5 часов",
    "10hours": "10 часов",
    "24hours": "24 часа",
    "forever": "Навсегда",
}

DURATION_SECONDS = {
    "10min": 600,
    "1hour": 3600,
    "5hours": 18000,
    "10hours": 36000,
    "24hours": 86400,
}

# -------------------------------------------------------------------
# Совместимые ChatPermissions
# -------------------------------------------------------------------
def get_mute_permissions():
    """Возвращает ChatPermissions с запретом всех сообщений."""
    params = {}
    # Базовые поля, которые есть всегда
    params['can_send_messages'] = False
    params['can_send_other_messages'] = False
    params['can_add_web_page_previews'] = False
    params['can_change_info'] = False
    params['can_invite_users'] = False
    params['can_pin_messages'] = False
    # Добавляем новые поля, если они поддерживаются библиотекой
    if hasattr(ChatPermissions, 'can_send_polls'):
        params['can_send_polls'] = False
    if hasattr(ChatPermissions, 'can_send_media_messages'):
        params['can_send_media_messages'] = False
    return ChatPermissions(**params)

def get_unmute_permissions():
    """Возвращает ChatPermissions для снятия мута."""
    params = {}
    params['can_send_messages'] = True
    params['can_send_other_messages'] = True
    params['can_add_web_page_previews'] = True
    params['can_change_info'] = False
    params['can_invite_users'] = True
    params['can_pin_messages'] = False
    if hasattr(ChatPermissions, 'can_send_polls'):
        params['can_send_polls'] = True
    if hasattr(ChatPermissions, 'can_send_media_messages'):
        params['can_send_media_messages'] = True
    return ChatPermissions(**params)

# -------------------------------------------------------------------
# Вспомогательные функции
# -------------------------------------------------------------------
async def delayed_task(delay: float, coro):
    await asyncio.sleep(delay)
    await coro()

async def resolve_user(text: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Ищет пользователя по @username или ID. Возвращает user_id или None."""
    text = text.strip()
    # Числовой ID
    try:
        user_id = int(text)
        await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return user_id
    except ValueError:
        pass
    except Exception:
        pass

    # Username
    username = text.lstrip('@')
    for try_name in (f"@{username}", username):
        try:
            user = await context.bot.get_chat(try_name)
            if user.type == 'private':
                await context.bot.get_chat_member(GROUP_CHAT_ID, user.id)
                return user.id
        except Exception:
            continue
    return None

async def is_user_admin(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        member = await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return member.status in ("creator", "administrator")
    except:
        return False

async def add_to_history(context: ContextTypes.DEFAULT_TYPE, buyer_id: int, product: str, details: str):
    db = load_db()
    db["history"].append({
        "buyer_id": buyer_id,
        "product": product,
        "details": details,
        "timestamp": datetime.utcnow().timestamp(),
    })
    save_db(db)

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        admins = await context.bot.get_chat_administrators(GROUP_CHAT_ID)
        admin_set = set(a.user.id for a in admins)
        for uid in ADMIN_IDS:
            admin_set.add(uid)
        for uid in admin_set:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
            except:
                pass
    except Exception as e:
        logging.error(f"Ошибка уведомления: {e}")

# -------------------------------------------------------------------
# Команды администратора /mute и /unmute
# -------------------------------------------------------------------
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Использование: /mute @username 10m (или 1h, forever)"""
    user_id = update.effective_user.id
    if not await is_user_admin(user_id, context):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Использование: /mute @username <длительность>\nПример: 10m, 1h, forever")
        return

    target_text = context.args[0]
    duration_str = context.args[1].lower()
    target_id = await resolve_user(target_text, context)
    if not target_id:
        await update.message.reply_text("❌ Пользователь не найден в группе.")
        return

    # Определяем длительность
    if duration_str == "forever":
        until_date = None
        until_db = None
        label = "Навсегда"
    else:
        try:
            if duration_str.endswith("m"):
                minutes = int(duration_str[:-1])
                seconds = minutes * 60
                label = f"{minutes} мин"
            elif duration_str.endswith("h"):
                hours = int(duration_str[:-1])
                seconds = hours * 3600
                label = f"{hours} ч"
            else:
                raise ValueError
        except:
            await update.message.reply_text("Неверный формат. Используйте: Xm (минуты) или Xh (часы) или forever.")
            return
        now = datetime.utcnow()
        until_date = int((now + timedelta(seconds=seconds)).timestamp())
        until_db = until_date

    # Применяем мут
    try:
        await context.bot.restrict_chat_member(
            chat_id=GROUP_CHAT_ID,
            user_id=target_id,
            permissions=get_mute_permissions(),
            until_date=until_date,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при муте: {e}")
        return

    # Сохраняем в БД
    db = load_db()
    db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
    db["mutes"].append({
        "target_id": target_id,
        "muter_id": user_id,
        "duration": "custom",
        "until_date": until_db,
    })
    save_db(db)

    # Планируем очистку записи из БД после истечения
    if until_db:
        async def cleanup():
            db = load_db()
            db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == target_id and m["until_date"] == until_db)]
            save_db(db)
        delay = until_db - datetime.utcnow().timestamp()
        if delay > 0:
            asyncio.create_task(delayed_task(delay, cleanup))

    # Уведомления
    try:
        target_user = await context.bot.get_chat(target_id)
        target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
    except:
        target_name = f"ID {target_id}"
    await update.message.reply_text(f"🔇 {target_name} замучен на {label}.")
    await notify_admins(context, f"🔇 Админ {update.effective_user.full_name} замутил {target_name} на {label}.")
    await context.bot.send_message(
        GROUP_CHAT_ID,
        f"🔇 {target_name} получил мут на {label}.",
    )

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Использование: /unmute @username"""
    user_id = update.effective_user.id
    if not await is_user_admin(user_id, context):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /unmute @username")
        return

    target_text = context.args[0]
    target_id = await resolve_user(target_text, context)
    if not target_id:
        await update.message.reply_text("❌ Пользователь не найден в группе.")
        return

    # Удаляем ограничения
    try:
        await context.bot.restrict_chat_member(
            chat_id=GROUP_CHAT_ID,
            user_id=target_id,
            permissions=get_unmute_permissions(),
            until_date=0,  # снимает все ограничения
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при размуте: {e}")
        return

    # Удаляем из БД
    db = load_db()
    initial_len = len(db["mutes"])
    db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
    if len(db["mutes"]) == initial_len:
        await update.message.reply_text("ℹ️ Пользователь не был замучен ботом.")
        return
    save_db(db)

    try:
        target_user = await context.bot.get_chat(target_id)
        target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
    except:
        target_name = f"ID {target_id}"
    await update.message.reply_text(f"🔊 Мут снят с {target_name}.")
    await notify_admins(context, f"🔊 Админ {update.effective_user.full_name} размутил {target_name}.")
    await context.bot.send_message(GROUP_CHAT_ID, f"🔊 {target_name} снова может писать.")

# -------------------------------------------------------------------
# Админ-панель
# -------------------------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_admin(user_id, context):
        await update.message.reply_text("⛔ Эта команда доступна только администраторам.")
        return

    db = load_db()
    text = "🛡️ *Панель администратора*\n\n"

    # Префиксы
    text += "*Активные префиксы:*\n"
    prefixes = db.get("prefixes", [])
    if prefixes:
        for p in prefixes:
            try:
                user = await context.bot.get_chat(p["user_id"])
                name = f"@{user.username}" if user.username else user.first_name
            except:
                name = f"ID {p['user_id']}"
            if p.get("expires_at"):
                exp = datetime.fromtimestamp(p["expires_at"]).strftime("%d.%m.%Y %H:%M")
                text += f"  {name} — {p['title']} до {exp}\n"
            else:
                text += f"  {name} — {p['title']} (навсегда)\n"
    else:
        text += "  (пусто)\n"

    # Муты
    text += "\n*Текущие муты:*\n"
    mutes = db.get("mutes", [])
    now_ts = datetime.utcnow().timestamp()
    active_mutes = [m for m in mutes if m.get("until_date") is None or m["until_date"] > now_ts]
    if active_mutes:
        for m in active_mutes:
            try:
                target = await context.bot.get_chat(m["target_id"])
                target_name = f"@{target.username}" if target.username else target.first_name
            except:
                target_name = f"ID {m['target_id']}"
            if m.get("until_date"):
                exp = datetime.fromtimestamp(m["until_date"]).strftime("%d.%m.%Y %H:%M")
                text += f"  {target_name} — до {exp}\n"
            else:
                text += f"  {target_name} — навсегда\n"
    else:
        text += "  (пусто)\n"

    # История
    history = db.get("history", [])
    text += "\n*Последние покупки:*\n"
    if history:
        for h in history[-10:]:
            try:
                buyer = await context.bot.get_chat(h["buyer_id"])
                buyer_name = f"@{buyer.username}" if buyer.username else buyer.first_name
            except:
                buyer_name = f"ID {h['buyer_id']}"
            ts = datetime.fromtimestamp(h["timestamp"]).strftime("%d.%m.%Y %H:%M")
            text += f"  {ts} — {buyer_name}: {h['product']} {h['details']}\n"
    else:
        text += "  (пусто)\n"

    await update.message.reply_text(text, parse_mode="Markdown")

# -------------------------------------------------------------------
# Магазин (меню и обработка)
# -------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Пожалуйста, используйте /start в личном чате со мной.")
        return
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🛒 Магазин", callback_data="shop")],
    ]
    await update.message.reply_text(
        "🎛️ *Панель управления*\nВыберите раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🛒 Магазин", callback_data="shop")],
    ]
    await query.edit_message_text(
        "🎛️ *Панель управления*\nВыберите раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db = load_db()
    prefix = next((p for p in db.get("prefixes", []) if p["user_id"] == user_id), None)
    mutes = [m for m in db.get("mutes", []) if m["muter_id"] == user_id]
    now = datetime.utcnow().timestamp()

    text = "👤 *Ваш профиль*\n\n"
    if prefix:
        if prefix.get("expires_at"):
            expire_time = datetime.fromtimestamp(prefix["expires_at"])
            remaining = expire_time - datetime.now()
            if remaining.total_seconds() > 0:
                text += f"🏷️ Префикс: {prefix['title']} (истекает через {str(remaining).split('.')[0]})\n"
            else:
                text += "🏷️ Префикс: истёк\n"
        else:
            text += f"🏷️ Префикс: {prefix['title']} (навсегда)\n"
    else:
        text += "🏷️ Нет активного префикса\n"

    if mutes:
        text += "\n🔇 *Купленные муты:*\n"
        for m in mutes:
            try:
                target = await context.bot.get_chat(m["target_id"])
                name = f"@{target.username}" if target.username else target.first_name
            except:
                name = f"ID {m['target_id']}"
            if m.get("until_date"):
                exp = datetime.fromtimestamp(m["until_date"])
                if m["until_date"] > now:
                    text += f"  → {name}: до {exp.strftime('%H:%M')}\n"
                else:
                    text += f"  → {name}: истёк\n"
            else:
                text += f"  → {name}: Навсегда\n"
    else:
        text += "\n🔇 Нет купленных мутов"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🏷️ Префикс", callback_data="shop_prefix")],
        [InlineKeyboardButton("🔇 Мут", callback_data="shop_mute")],
        [InlineKeyboardButton("🔊 Размут", callback_data="shop_unmute")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ]
    await query.edit_message_text(
        "🛒 *Магазин*\nВыберите товар:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

# Карточки товаров
async def show_prefix_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🏷️ *Префикс*\n"
        "────────────────\n"
        "Получите зелёный custom title в группе.\n"
        "────────────────\n"
        "Цены:\n"
        "10 мин — 50 ⭐\n"
        "1 час — 80 ⭐\n"
        "5 часов — 150 ⭐\n"
        "10 часов — 250 ⭐\n"
        "24 часа — 350 ⭐\n"
        "Навсегда — 400 ⭐\n"
        "Выберите длительность:"
    )
    keyboard = [
        [InlineKeyboardButton("10 мин · 50⭐", callback_data="prefix_dur_10min")],
        [InlineKeyboardButton("1 час · 80⭐", callback_data="prefix_dur_1hour")],
        [InlineKeyboardButton("5 часов · 150⭐", callback_data="prefix_dur_5hours")],
        [InlineKeyboardButton("10 часов · 250⭐", callback_data="prefix_dur_10hours")],
        [InlineKeyboardButton("24 часа · 350⭐", callback_data="prefix_dur_24hours")],
        [InlineKeyboardButton("Навсегда · 400⭐", callback_data="prefix_dur_forever")],
        [InlineKeyboardButton("🔙 Назад в магазин", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_prefix_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dur = query.data.split("_")[2]
    await query.edit_message_text("💳 Отправляю счёт...")
    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title="Покупка префикса",
        description=f"Зелёный префикс на {DURATION_LABELS[dur]}",
        payload=f"prefix_{dur}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Префикс", PREFIX_PRICES[dur])],
        start_parameter="prefix",
    )

async def show_mute_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🔇 *Мут*\n"
        "────────────────\n"
        "Замутить любого участника группы.\n"
        "────────────────\n"
        "Цены:\n"
        "10 мин — 50 ⭐\n"
        "1 час — 100 ⭐\n"
        "5 часов — 200 ⭐\n"
        "10 часов — 250 ⭐\n"
        "24 часа — 300 ⭐\n"
        "Навсегда — 1000 ⭐\n"
        "Сначала выберите длительность:"
    )
    keyboard = [
        [InlineKeyboardButton("10 мин · 50⭐", callback_data="mute_dur_10min")],
        [InlineKeyboardButton("1 час · 100⭐", callback_data="mute_dur_1hour")],
        [InlineKeyboardButton("5 часов · 200⭐", callback_data="mute_dur_5hours")],
        [InlineKeyboardButton("10 часов · 250⭐", callback_data="mute_dur_10hours")],
        [InlineKeyboardButton("24 часа · 300⭐", callback_data="mute_dur_24hours")],
        [InlineKeyboardButton("Навсегда · 1000⭐", callback_data="mute_dur_forever")],
        [InlineKeyboardButton("🔙 Назад в магазин", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dur = query.data.split("_")[2]
    context.user_data["pending_mute"] = dur
    await query.edit_message_text(
        "Отправьте @username или числовой ID пользователя, которого хотите замутить.\n"
        "Пример: `@username` или `123456789`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="shop")]]),
    )

async def show_unmute_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🔊 *Размут*\n"
        "────────────────\n"
        "Снять мут с пользователя.\n"
        "Цена: 70 ⭐\n"
        "────────────────\n"
        "Отправьте @username или ID человека, которого нужно размутить:"
    )
    context.user_data["pending_unmute"] = True
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="shop")]]),
    )

# Обработчик ввода цели
async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    user_data = context.user_data
    msg_text = update.message.text.strip()

    if "pending_mute" in user_data:
        dur = user_data.pop("pending_mute")
        target_id = await resolve_user(msg_text, context)
        if not target_id:
            await update.message.reply_text("❌ Пользователь не найден в группе.")
            return

        db = load_db()
        if any(m["target_id"] == target_id for m in db.get("mutes", [])):
            await update.message.reply_text("🔇 Этот пользователь уже замучен.")
            return

        amount = MUTE_PRICES[dur]
        await update.message.reply_invoice(
            title="Покупка мута",
            description=f"Мут на {DURATION_LABELS[dur]}",
            payload=f"mute_{dur}_{target_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Мут", amount)],
            start_parameter="mute",
        )

    elif "pending_unmute" in user_data:
        del user_data["pending_unmute"]
        target_id = await resolve_user(msg_text, context)
        if not target_id:
            await update.message.reply_text("❌ Пользователь не найден в группе.")
            return

        db = load_db()
        if not any(m["target_id"] == target_id for m in db.get("mutes", [])):
            await update.message.reply_text("🔊 Этот пользователь не замучен ботом.")
            return

        await update.message.reply_invoice(
            title="Размут",
            description="Снять мут с пользователя",
            payload=f"unmute_{target_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Размут", UNMUTE_PRICE)],
            start_parameter="unmute",
        )

# -------------------------------------------------------------------
# Платёжные обработчики
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

    await update.message.reply_text("✅ Платёж успешен! Обрабатываю...")

    if payload.startswith("prefix_"):
        dur = payload.split("_")[1]
        title = "🟢 Premium"
        expires_in = None if dur == "forever" else DURATION_SECONDS[dur]

        # Установка префикса
        try:
            await context.bot.promote_chat_member(
                chat_id=GROUP_CHAT_ID,
                user_id=buyer.id,
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
                user_id=buyer.id,
                custom_title=title,
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось установить префикс: {e}")
            return

        # Сохраняем в БД
        db = load_db()
        db.setdefault("prefixes", []).append({
            "user_id": buyer.id,
            "title": title,
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).timestamp() if expires_in else None,
            "purchase_id": payload,
        })
        save_db(db)
        await add_to_history(context, buyer.id, "Префикс", f"на {DURATION_LABELS[dur]}")

        # Если временный, планируем снятие
        if expires_in:
            async def demote():
                try:
                    await context.bot.promote_chat_member(
                        chat_id=GROUP_CHAT_ID,
                        user_id=buyer.id,
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
                    db2["prefixes"] = [p for p in db2["prefixes"] if not (p["user_id"] == buyer.id and p["title"] == title)]
                    save_db(db2)
                except Exception as e:
                    logging.error(f"Ошибка снятия префикса: {e}")
            asyncio.create_task(delayed_task(expires_in, demote))

        # Уведомления
        await notify_admins(context, f"🟢 {buyer_name} ({buyer_mention}) купил префикс на {DURATION_LABELS[dur]}.")
        await context.bot.send_message(
            GROUP_CHAT_ID,
            f"🎉 {buyer_mention} приобрёл зелёный префикс на {DURATION_LABELS[dur]}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Сделать так же", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

    elif payload.startswith("mute_"):
        _, dur, target_id_str = payload.split("_")
        target_id = int(target_id_str)
        now = datetime.utcnow()
        if dur == "forever":
            until_date = None
            until_db = None
        else:
            seconds = DURATION_SECONDS[dur]
            until_date = int((now + timedelta(seconds=seconds)).timestamp())
            until_db = until_date

        # Применяем мут
        try:
            await context.bot.restrict_chat_member(
                chat_id=GROUP_CHAT_ID,
                user_id=target_id,
                permissions=get_mute_permissions(),
                until_date=until_date,
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось замутить: {e}")
            return

        # Сохраняем в БД
        db = load_db()
        db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
        db["mutes"].append({
            "target_id": target_id,
            "muter_id": buyer.id,
            "duration": dur,
            "until_date": until_db,
        })
        save_db(db)

        # Планируем очистку записи
        if until_db:
            async def cleanup():
                db2 = load_db()
                db2["mutes"] = [m for m in db2["mutes"] if not (m["target_id"] == target_id and m["until_date"] == until_db)]
                save_db(db2)
            delay = until_db - now.timestamp()
            if delay > 0:
                asyncio.create_task(delayed_task(delay, cleanup))

        # Уведомления
        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
        except:
            target_name = f"ID {target_id}"
        await add_to_history(context, buyer.id, "Мут", f"{target_name} на {DURATION_LABELS[dur]}")
        await notify_admins(context, f"🔇 {buyer_name} ({buyer_mention}) замутил {target_name} на {DURATION_LABELS[dur]}.")
        await context.bot.send_message(
            GROUP_CHAT_ID,
            f"🔇 {buyer_mention} замутил {target_name} на {DURATION_LABELS[dur]}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Сделать так же", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

    elif payload.startswith("unmute_"):
        target_id = int(payload.split("_")[1])
        # Снимаем ограничения
        try:
            await context.bot.restrict_chat_member(
                chat_id=GROUP_CHAT_ID,
                user_id=target_id,
                permissions=get_unmute_permissions(),
                until_date=0,
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось размутить: {e}")
            return

        # Удаляем из БД
        db = load_db()
        db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
        save_db(db)

        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
        except:
            target_name = f"ID {target_id}"
        await add_to_history(context, buyer.id, "Размут", target_name)
        await notify_admins(context, f"🔊 {buyer_name} ({buyer_mention}) размутил {target_name}.")
        await context.bot.send_message(
            GROUP_CHAT_ID,
            f"🔊 {buyer_mention} размутил {target_name}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Сделать так же", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

# -------------------------------------------------------------------
# Восстановление отложенных задач при запуске
# -------------------------------------------------------------------
async def restore_scheduled_jobs(app: Application):
    db = load_db()
    now = datetime.utcnow().timestamp()

    for prefix in db.get("prefixes", []):
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
                        db2["prefixes"] = [p for p in db2["prefixes"] if not (p["user_id"] == uid and p["title"] == title)]
                        save_db(db2)
                    except Exception as e:
                        logging.error(f"Ошибка восстановленного снятия префикса: {e}")
                asyncio.create_task(delayed_task(delay, demote_restored))

    for mute in db.get("mutes", []):
        if mute.get("until_date"):
            delay = mute["until_date"] - now
            if delay > 0:
                async def cleanup_restored(tid=mute["target_id"], until=mute["until_date"]):
                    db2 = load_db()
                    db2["mutes"] = [m for m in db2["mutes"] if not (m["target_id"] == tid and m["until_date"] == until)]
                    save_db(db2)
                asyncio.create_task(delayed_task(delay, cleanup_restored))

# -------------------------------------------------------------------
# Обработчик кнопок
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
# Запуск
# -------------------------------------------------------------------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_target_input))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    async def post_init(application):
        await restore_scheduled_jobs(application)
    app.post_init = post_init

    app.run_polling()

if __name__ == "__main__":
    main()
