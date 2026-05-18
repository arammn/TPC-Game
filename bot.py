import asyncio
import logging
from datetime import datetime, timedelta, timezone
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

import config
from db import load_db, save_db

# -------------------------------------------------------------------
# Наборы прав для мута/размута
# -------------------------------------------------------------------
MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

UNMUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)

# -------------------------------------------------------------------
# Цены и длительности
# -------------------------------------------------------------------
PREFIX_PRICES = {
    "10min": 1, "1hour": 80, "5hours": 150,
    "10hours": 250, "24hours": 350, "forever": 400,
}
MUTE_PRICES = {
    "10min": 1, "1hour": 100, "5hours": 200,
    "10hours": 250, "24hours": 300, "forever": 1000,
}
UNMUTE_PRICE = 1

DURATION_LABELS = {
    "10min": "10 минут", "1hour": "1 час", "5hours": "5 часов",
    "10hours": "10 часов", "24hours": "24 часа", "forever": "Навсегда",
}
DURATION_SECONDS = {
    "10min": 600, "1hour": 3600, "5hours": 18000,
    "10hours": 36000, "24hours": 86400,
}

# -------------------------------------------------------------------
# Утилиты
# -------------------------------------------------------------------
async def delayed_task(delay: float, coro):
    await asyncio.sleep(delay)
    await coro()

async def resolve_user(text: str, context: ContextTypes.DEFAULT_TYPE, group_id: int) -> Optional[int]:
    """Поиск пользователя: числовой ID или @username (используем get_chat как в примере)."""
    text = text.strip()
    # 1. Числовой ID
    if text.isdigit():
        user_id = int(text)
        try:
            await context.bot.get_chat_member(group_id, user_id)
            return user_id
        except:
            pass

    # 2. Username – берём пример /id
    username = text.lstrip('@')
    if not username:
        return None

    # Пробуем получить chat с @username
    try:
        chat = await context.bot.get_chat(f"@{username}")
        if chat.type == "private":
            # Проверяем, что он в группе
            await context.bot.get_chat_member(group_id, chat.id)
            return chat.id
    except Exception:
        pass

    return None

async def is_user_admin(user_id: int, context: ContextTypes.DEFAULT_TYPE, group_id: int) -> bool:
    if user_id in config.ADMIN_IDS:
        return True
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
        return member.status in ("creator", "administrator")
    except:
        return False

async def is_group_registered(group_id: int) -> bool:
    db = load_db()
    return group_id in db.get("registered_groups", [])

async def get_user_groups(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> List[int]:
    db = load_db()
    registered = db.get("registered_groups", [])
    user_groups = []
    for gid in registered:
        try:
            member = await context.bot.get_chat_member(gid, user_id)
            if member.status in ("member", "administrator", "creator"):
                user_groups.append(gid)
        except:
            continue
    return user_groups

async def get_group_name(context, gid: int) -> str:
    try:
        chat = await context.bot.get_chat(gid)
        return chat.title or f"ID {gid}"
    except:
        return f"ID {gid}"

async def add_to_history(context, buyer_id, group_id, product, details):
    db = load_db()
    db["history"].append({
        "buyer_id": buyer_id,
        "group_id": group_id,
        "product": product,
        "details": details,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    })
    save_db(db)

async def notify_admins(context, group_id: int, text: str):
    try:
        admins = await context.bot.get_chat_administrators(group_id)
        ids = set(a.user.id for a in admins)
        for uid in config.ADMIN_IDS:
            ids.add(uid)
        for uid in ids:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
            except:
                pass
    except Exception as e:
        logging.error(f"Notify admins error: {e}")

# -------------------------------------------------------------------
# Префикс
# -------------------------------------------------------------------
async def give_prefix(context, group_id: int, user_id: int, title: str) -> bool:
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
    except Exception as e:
        raise Exception(f"Не удалось получить информацию: {e}")

    if member.status == "creator":
        raise Exception("Невозможно установить префикс создателю группы.")

    if member.status not in ("administrator", "creator"):
        try:
            await context.bot.promote_chat_member(
                chat_id=group_id,
                user_id=user_id,
                can_change_info=False,
                can_delete_messages=False,
                can_invite_users=False,
                can_restrict_members=False,
                can_pin_messages=False,
                can_promote_members=False,
                can_manage_video_chats=False,
                can_manage_chat=False,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
            )
        except Exception as e:
            raise Exception(f"Не удалось повысить: {e}")

    try:
        await context.bot.set_chat_administrator_custom_title(
            chat_id=group_id,
            user_id=user_id,
            custom_title=title,
        )
    except Exception as e:
        raise Exception(f"Не удалось установить префикс: {e}")
    return True

async def remove_prefix(context, group_id: int, user_id: int):
    try:
        await context.bot.promote_chat_member(
            chat_id=group_id,
            user_id=user_id,
            can_change_info=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_video_chats=False,
            can_manage_chat=False,
            is_anonymous=False,
        )
    except Exception as e:
        logging.error(f"Demote error: {e}")
    try:
        await context.bot.ban_chat_member(chat_id=group_id, user_id=user_id, until_date=0)
        await context.bot.unban_chat_member(chat_id=group_id, user_id=user_id, only_if_banned=True)
    except Exception as e:
        logging.error(f"Ban/unban error: {e}")

# -------------------------------------------------------------------
# Регистрация группы
# -------------------------------------------------------------------
async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эту команду можно использовать только в группе.")
        return
    user_id = update.effective_user.id
    if not await is_user_admin(user_id, context, chat_id):
        await update.message.reply_text("⛔ Только администратор может зарегистрировать группу.")
        return

    db = load_db()
    if "registered_groups" not in db:
        db["registered_groups"] = []
    if chat_id in db["registered_groups"]:
        await update.message.reply_text("ℹ️ Эта группа уже зарегистрирована.")
        return
    db["registered_groups"].append(chat_id)
    save_db(db)
    await update.message.reply_text("✅ Группа зарегистрирована!")

# -------------------------------------------------------------------
# Команды в группе (бесплатные)
# -------------------------------------------------------------------
async def cmd_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not await is_group_registered(group_id):
        await update.message.reply_text("❌ Группа не зарегистрирована. /register")
        return
    if not await is_user_admin(update.effective_user.id, context, group_id):
        await update.message.reply_text("⛔ Только для админов.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /prefix @username Название")
        return

    username = context.args[0].replace("@", "")
    title = " ".join(context.args[1:])
    member = None
    try:
        admins = await context.bot.get_chat_administrators(group_id)
        for admin in admins:
            user = admin.user
            if user.username and user.username.lower() == username.lower():
                member = user
                break
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return

    if not member:
        await update.message.reply_text("❌ Пользователь не найден среди администраторов.")
        return

    try:
        await give_prefix(context, group_id, member.id, title)
        await update.message.reply_text(f"🏷️ Префикс '{title}' выдан @{username}.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not await is_group_registered(group_id):
        await update.message.reply_text("❌ Группа не зарегистрирована.")
        return
    if not await is_user_admin(update.effective_user.id, context, group_id):
        await update.message.reply_text("⛔ Только для админов.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /mute @username <10m|1h|forever>")
        return
    target_text = context.args[0]
    dur_str = context.args[1].lower()
    uid = await resolve_user(target_text, context, group_id)
    if not uid:
        await update.message.reply_text("❌ Пользователь не найден.")
        return

    if dur_str == "forever":
        until_date = None
        until_db = None
        label = "навсегда"
    else:
        try:
            if dur_str.endswith("m"):
                seconds = int(dur_str[:-1]) * 60
            elif dur_str.endswith("h"):
                seconds = int(dur_str[:-1]) * 3600
            else:
                raise ValueError
            until_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            until_date = int(until_dt.timestamp())
            until_db = until_dt.timestamp()
            label = dur_str
        except:
            await update.message.reply_text("Неверный формат. Примеры: 10m, 1h, forever")
            return

    try:
        await context.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=uid,
            permissions=MUTE_PERMISSIONS,
            until_date=until_date,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось замутить: {e}")
        return

    db = load_db()
    db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == uid and m.get("group_id") == group_id)]
    db["mutes"].append({
        "target_id": uid,
        "muter_id": update.effective_user.id,
        "group_id": group_id,
        "duration": "custom",
        "until_date": until_db,
    })
    save_db(db)

    try:
        user = await context.bot.get_chat(uid)
        name = f"@{user.username}" if user.username else user.first_name
    except:
        name = f"ID {uid}"
    await update.message.reply_text(f"🔇 {name} замучен на {label}.")
    await notify_admins(context, group_id, f"🔇 Админ замутил {name} на {label}.")
    await context.bot.send_message(group_id, f"🔇 {name} получил мут на {label}.")

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not await is_group_registered(group_id):
        await update.message.reply_text("❌ Группа не зарегистрирована.")
        return
    if not await is_user_admin(update.effective_user.id, context, group_id):
        await update.message.reply_text("⛔ Только для админов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /unmute @username")
        return
    uid = await resolve_user(context.args[0], context, group_id)
    if not uid:
        await update.message.reply_text("❌ Пользователь не найден.")
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=uid,
            permissions=UNMUTE_PERMISSIONS,
            until_date=0,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    db = load_db()
    db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == uid and m.get("group_id") == group_id)]
    save_db(db)

    try:
        user = await context.bot.get_chat(uid)
        name = f"@{user.username}" if user.username else user.first_name
    except:
        name = f"ID {uid}"
    await update.message.reply_text(f"🔊 Мут снят с {name}.")
    await notify_admins(context, group_id, f"🔊 Админ размутил {name}.")
    await context.bot.send_message(group_id, f"🔊 {name} снова может писать.")

# -------------------------------------------------------------------
# Личный кабинет – выбор группы и главное меню
# -------------------------------------------------------------------
async def show_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    groups = await get_user_groups(user_id, context)
    if not groups:
        text = "❌ Вы не состоите ни в одной зарегистрированной группе."
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    keyboard = []
    for gid in groups:
        name = await get_group_name(context, gid)
        keyboard.append([InlineKeyboardButton(name, callback_data=f"select_group_{gid}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_selection")])

    text = "Выберите группу для взаимодействия:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Пожалуйста, используйте /start в личном чате.")
        return
    await show_group_selection(update, context)

async def select_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "cancel_selection":
        await query.edit_message_text("Действие отменено.")
        return
    gid = int(data.split("_")[2])
    context.user_data["selected_group"] = gid
    await query.edit_message_text(f"✅ Группа выбрана: {await get_group_name(context, gid)}")
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🛒 Магазин", callback_data="shop")],
        [InlineKeyboardButton("🛡️ Админ-панель", callback_data="admin_panel")],
        [InlineKeyboardButton("🔄 Сменить группу", callback_data="change_group")],
    ]
    text = "🎛️ *Главное меню*\nВыберите раздел:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# -------------------------------------------------------------------
# Профиль, Магазин, Админ-панель (в личке)
# -------------------------------------------------------------------
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    group_id = context.user_data.get("selected_group")
    if not group_id:
        await query.edit_message_text("Сначала выберите группу.")
        return

    db = load_db()
    prefix = next((p for p in db["prefixes"] if p["user_id"] == user_id and p.get("group_id") == group_id), None)
    mutes = [m for m in db["mutes"] if m["muter_id"] == user_id and m.get("group_id") == group_id]
    now = datetime.now(timezone.utc).timestamp()

    text = f"👤 *Профиль*\nГруппа: {await get_group_name(context, group_id)}\n\n"
    if prefix:
        if prefix.get("expires_at"):
            exp = datetime.fromtimestamp(prefix["expires_at"], tz=timezone.utc)
            remaining = exp - datetime.now(timezone.utc)
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
                user = await context.bot.get_chat(m["target_id"])
                name = f"@{user.username}" if user.username else user.first_name
            except:
                name = f"ID {m['target_id']}"
            if m.get("until_date"):
                if m["until_date"] > now:
                    exp = datetime.fromtimestamp(m["until_date"], tz=timezone.utc).strftime("%H:%M")
                    text += f"  → {name}: до {exp}\n"
                else:
                    text += f"  → {name}: истёк\n"
            else:
                text += f"  → {name}: Навсегда\n"
    else:
        text += "\n🔇 Нет купленных мутов"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🏷️ Префикс", callback_data="buy_prefix")],
        [InlineKeyboardButton("🔇 Мут", callback_data="buy_mute")],
        [InlineKeyboardButton("🔊 Размут", callback_data="buy_unmute")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ]
    await query.edit_message_text("🛒 *Магазин*\nВыберите товар:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def buy_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    group_id = context.user_data.get("selected_group")
    if not group_id:
        await query.edit_message_text("Сначала выберите группу.")
        return
    context.user_data["buy_product"] = "prefix"
    text = (
        "🏷️ *Префикс*\n"
        "Выберите длительность:\n"
        "10 мин — 50⭐ | 1 час — 80⭐ | 5 часов — 150⭐\n"
        "10 часов — 250⭐ | 24 часа — 350⭐ | Навсегда — 400⭐"
    )
    keyboard = [
        [InlineKeyboardButton("10 мин", callback_data="prefix_dur_10min"),
         InlineKeyboardButton("1 час", callback_data="prefix_dur_1hour")],
        [InlineKeyboardButton("5 часов", callback_data="prefix_dur_5hours"),
         InlineKeyboardButton("10 часов", callback_data="prefix_dur_10hours")],
        [InlineKeyboardButton("24 часа", callback_data="prefix_dur_24hours"),
         InlineKeyboardButton("Навсегда", callback_data="prefix_dur_forever")],
        [InlineKeyboardButton("🔙 Назад", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def buy_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    group_id = context.user_data.get("selected_group")
    if not group_id:
        await query.edit_message_text("Сначала выберите группу.")
        return
    context.user_data["buy_product"] = "mute"
    text = (
        "🔇 *Мут*\n"
        "Выберите длительность:\n"
        "10 мин — 50⭐ | 1 час — 100⭐ | 5 часов — 200⭐\n"
        "10 часов — 250⭐ | 24 часа — 300⭐ | Навсегда — 1000⭐"
    )
    keyboard = [
        [InlineKeyboardButton("10 мин", callback_data="mute_dur_10min"),
         InlineKeyboardButton("1 час", callback_data="mute_dur_1hour")],
        [InlineKeyboardButton("5 часов", callback_data="mute_dur_5hours"),
         InlineKeyboardButton("10 часов", callback_data="mute_dur_10hours")],
        [InlineKeyboardButton("24 часа", callback_data="mute_dur_24hours"),
         InlineKeyboardButton("Навсегда", callback_data="mute_dur_forever")],
        [InlineKeyboardButton("🔙 Назад", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def buy_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    group_id = context.user_data.get("selected_group")
    if not group_id:
        await query.edit_message_text("Сначала выберите группу.")
        return
    context.user_data["buy_product"] = "unmute"
    context.user_data["pending_unmute"] = True
    await query.edit_message_text(
        "🔊 *Размут*\nЦена: 70⭐\nОтправьте @username или ID пользователя для снятия мута:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="shop")]]),
    )

async def handle_duration_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    product = context.user_data.get("buy_product")
    if not product:
        await query.edit_message_text("Ошибка, начните заново из магазина.")
        return

    if product == "prefix" and data.startswith("prefix_dur_"):
        dur = data.split("_")[2]
        context.user_data["duration"] = dur
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
    elif product == "mute" and data.startswith("mute_dur_"):
        dur = data.split("_")[2]
        context.user_data["duration"] = dur
        context.user_data["pending_mute"] = True
        await query.edit_message_text(
            "Отправьте @username или ID пользователя для мута:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="shop")]]),
        )
    else:
        await query.edit_message_text("Неверное действие.")

async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    group_id = context.user_data.get("selected_group")
    if not group_id:
        await update.message.reply_text("Сначала выберите группу через /start.")
        return

    user_data = context.user_data
    msg_text = update.message.text.strip()

    if "pending_mute" in user_data:
        dur = user_data.pop("duration", None)
        if not dur:
            await update.message.reply_text("Ошибка, попробуйте снова.")
            return
        target_id = await resolve_user(msg_text, context, group_id)
        if not target_id:
            await update.message.reply_text("❌ Пользователь не найден.")
            return
        db = load_db()
        if any(m["target_id"] == target_id and m.get("group_id") == group_id for m in db.get("mutes", [])):
            await update.message.reply_text("🔇 Уже замучен.")
            return
        amount = MUTE_PRICES[dur]
        await update.message.reply_invoice(
            title="Покупка мута",
            description=f"Мут на {DURATION_LABELS[dur]}",
            payload=f"mute_{dur}_{target_id}_{group_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Мут", amount)],
            start_parameter="mute",
        )
    elif "pending_unmute" in user_data:
        del user_data["pending_unmute"]
        target_id = await resolve_user(msg_text, context, group_id)
        if not target_id:
            await update.message.reply_text("❌ Пользователь не найден.")
            return
        db = load_db()
        if not any(m["target_id"] == target_id and m.get("group_id") == group_id for m in db.get("mutes", [])):
            await update.message.reply_text("🔊 Не замучен.")
            return
        await update.message.reply_invoice(
            title="Размут",
            description="Снять мут",
            payload=f"unmute_{target_id}_{group_id}",
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
    group_id = context.user_data.get("selected_group")

    if not group_id:
        await update.message.reply_text("Ошибка: группа не выбрана.")
        return

    await update.message.reply_text("✅ Платёж успешен! Обрабатываю...")

    if payload.startswith("prefix_"):
        dur = payload.split("_")[1]
        title = "🟢 Premium"
        expires_in = None if dur == "forever" else DURATION_SECONDS[dur]

        try:
            await give_prefix(context, group_id, buyer.id, title)
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось: {e}")
            return

        db = load_db()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).timestamp() if expires_in else None
        db["prefixes"].append({
            "user_id": buyer.id,
            "group_id": group_id,
            "title": title,
            "expires_at": expires_at,
        })
        save_db(db)
        await add_to_history(context, buyer.id, group_id, "Префикс", f"на {DURATION_LABELS[dur]}")

        if expires_in:
            async def demote():
                await remove_prefix(context, group_id, buyer.id)
                db2 = load_db()
                db2["prefixes"] = [p for p in db2["prefixes"] if not (p["user_id"] == buyer.id and p.get("group_id") == group_id)]
                save_db(db2)
            asyncio.create_task(delayed_task(expires_in, demote))

        await notify_admins(context, group_id, f"🟢 {buyer_name} купил префикс на {DURATION_LABELS[dur]}.")
        await context.bot.send_message(group_id, f"🎉 {buyer_mention} приобрёл зелёный префикс на {DURATION_LABELS[dur]}.")

    elif payload.startswith("mute_"):
        parts = payload.split("_")
        dur = parts[1]
        target_id = int(parts[2])
        group_id = int(parts[3])
        if dur == "forever":
            until_date = None
            until_db = None
        else:
            seconds = DURATION_SECONDS[dur]
            until_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            until_date = int(until_dt.timestamp())
            until_db = until_dt.timestamp()

        try:
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=target_id,
                permissions=MUTE_PERMISSIONS,
                until_date=until_date,
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось замутить: {e}")
            return

        db = load_db()
        db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == target_id and m.get("group_id") == group_id)]
        db["mutes"].append({
            "target_id": target_id,
            "muter_id": buyer.id,
            "group_id": group_id,
            "duration": dur,
            "until_date": until_db,
        })
        save_db(db)

        if until_db:
            async def cleanup():
                db2 = load_db()
                db2["mutes"] = [m for m in db2["mutes"] if not (m["target_id"] == target_id and m["until_date"] == until_db and m.get("group_id") == group_id)]
                save_db(db2)
            asyncio.create_task(delayed_task(until_db - datetime.now(timezone.utc).timestamp(), cleanup))

        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
        except:
            target_name = f"ID {target_id}"
        await add_to_history(context, buyer.id, group_id, "Мут", f"{target_name} на {DURATION_LABELS[dur]}")
        await notify_admins(context, group_id, f"🔇 {buyer_name} замутил {target_name} на {DURATION_LABELS[dur]}.")
        await context.bot.send_message(group_id, f"🔇 {buyer_mention} замутил {target_name} на {DURATION_LABELS[dur]}.")

    elif payload.startswith("unmute_"):
        parts = payload.split("_")
        target_id = int(parts[1])
        group_id = int(parts[2])
        try:
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=target_id,
                permissions=UNMUTE_PERMISSIONS,
                until_date=0,
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось размутить: {e}")
            return

        db = load_db()
        db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == target_id and m.get("group_id") == group_id)]
        save_db(db)

        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
        except:
            target_name = f"ID {target_id}"
        await add_to_history(context, buyer.id, group_id, "Размут", target_name)
        await notify_admins(context, group_id, f"🔊 {buyer_name} размутил {target_name}.")
        await context.bot.send_message(group_id, f"🔊 {buyer_mention} размутил {target_name}.")

# -------------------------------------------------------------------
# Админ-панель (в личке)
# -------------------------------------------------------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    group_id = context.user_data.get("selected_group")
    if not group_id:
        await query.edit_message_text("Сначала выберите группу.")
        return
    await show_admin_stats(update, context, group_id)

async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: int):
    db = load_db()
    gname = await get_group_name(context, group_id)
    text = f"🛡️ *Панель администратора*\nГруппа: {gname}\n\n"

    prefixes = [p for p in db["prefixes"] if p.get("group_id") == group_id]
    text += "*Префиксы:*\n"
    for p in prefixes:
        try:
            user = await context.bot.get_chat(p["user_id"])
            name = f"@{user.username}" if user.username else user.first_name
        except:
            name = f"ID {p['user_id']}"
        if p.get("expires_at"):
            exp = datetime.fromtimestamp(p["expires_at"], tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
            text += f"  {name} — {p['title']} до {exp}\n"
        else:
            text += f"  {name} — {p['title']} (навсегда)\n"
    if not prefixes:
        text += "  (пусто)\n"

    mutes = [m for m in db["mutes"] if m.get("group_id") == group_id]
    now_ts = datetime.now(timezone.utc).timestamp()
    active = [m for m in mutes if m.get("until_date") is None or m["until_date"] > now_ts]
    text += "\n*Муты:*\n"
    for m in active:
        try:
            user = await context.bot.get_chat(m["target_id"])
            name = f"@{user.username}" if user.username else user.first_name
        except:
            name = f"ID {m['target_id']}"
        if m.get("until_date"):
            exp = datetime.fromtimestamp(m["until_date"], tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
            text += f"  {name} — до {exp}\n"
        else:
            text += f"  {name} — навсегда\n"
    if not active:
        text += "  (пусто)\n"

    history = [h for h in db["history"] if h.get("group_id") == group_id]
    text += "\n*Последние покупки:*\n"
    for h in history[-10:]:
        try:
            buyer = await context.bot.get_chat(h["buyer_id"])
            bname = f"@{buyer.username}" if buyer.username else buyer.first_name
        except:
            bname = f"ID {h['buyer_id']}"
        ts = datetime.fromtimestamp(h["timestamp"], tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
        text += f"  {ts} — {bname}: {h['product']} {h['details']}\n"
    if not history:
        text += "  (пусто)\n"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# -------------------------------------------------------------------
# Обработчик кнопок
# -------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "main_menu":
        await show_main_menu(update, context)
    elif data == "profile":
        await profile(update, context)
    elif data == "shop":
        await shop(update, context)
    elif data == "buy_prefix":
        await buy_prefix(update, context)
    elif data == "buy_mute":
        await buy_mute(update, context)
    elif data == "buy_unmute":
        await buy_unmute(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "change_group":
        context.user_data.pop("selected_group", None)
        await show_group_selection(update, context)
    elif data.startswith("select_group_"):
        await select_group_callback(update, context)
    elif data.startswith("prefix_dur_") or data.startswith("mute_dur_"):
        await handle_duration_selection(update, context)
    else:
        await query.answer("Неизвестная команда.")

# -------------------------------------------------------------------
# Восстановление задач
# -------------------------------------------------------------------
async def restore_scheduled_jobs(app: Application):
    db = load_db()
    now = datetime.now(timezone.utc).timestamp()
    for p in db.get("prefixes", []):
        if p.get("expires_at") and p.get("group_id"):
            delay = p["expires_at"] - now
            if delay > 0:
                async def demote_restored(uid=p["user_id"], gid=p["group_id"]):
                    try:
                        await remove_prefix(app.bot, gid, uid)
                    except Exception as e:
                        logging.error(f"Restore demote error: {e}")
                    db2 = load_db()
                    db2["prefixes"] = [x for x in db2["prefixes"] if not (x["user_id"] == uid and x.get("group_id") == gid)]
                    save_db(db2)
                asyncio.create_task(delayed_task(delay, demote_restored))
    for m in db.get("mutes", []):
        if m.get("until_date") and m.get("group_id"):
            delay = m["until_date"] - now
            if delay > 0:
                async def cleanup_restored(tid=m["target_id"], until=m["until_date"], gid=m["group_id"]):
                    db2 = load_db()
                    db2["mutes"] = [x for x in db2["mutes"] if not (x["target_id"] == tid and x["until_date"] == until and x.get("group_id") == gid)]
                    save_db(db2)
                asyncio.create_task(delayed_task(delay, cleanup_restored))

# -------------------------------------------------------------------
# Запуск
# -------------------------------------------------------------------
def main():
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("prefix", cmd_prefix))
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
