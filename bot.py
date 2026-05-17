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
    "10min": 50, "1hour": 80, "5hours": 150,
    "10hours": 250, "24hours": 350, "forever": 400,
}
MUTE_PRICES = {
    "10min": 50, "1hour": 100, "5hours": 200,
    "10hours": 250, "24hours": 300, "forever": 1000,
}
UNMUTE_PRICE = 70

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
    """Поиск пользователя в конкретной группе по @username или числовому ID."""
    text = text.strip()
    # 1. Числовой ID
    if text.isdigit():
        user_id = int(text)
        try:
            await context.bot.get_chat_member(group_id, user_id)
            return user_id
        except:
            pass

    # 2. Username
    username = text.lstrip('@')
    if not username:
        return None

    for variant in (f"@{username}", username):
        try:
            member = await context.bot.get_chat_member(group_id, variant)
            return member.user.id
        except:
            continue
    try:
        user = await context.bot.get_chat(f"@{username}")
        if user.type == "private":
            await context.bot.get_chat_member(group_id, user.id)
            return user.id
    except:
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
# Префикс (исправленная версия)
# -------------------------------------------------------------------
async def give_prefix(context, group_id: int, user_id: int, title: str) -> bool:
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
    except Exception as e:
        raise Exception(f"Не удалось получить информацию о пользователе: {e}")

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
            raise Exception(f"Не удалось повысить пользователя: {e}")

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
        await context.bot.ban_chat_member(
            chat_id=group_id,
            user_id=user_id,
            until_date=0,
        )
        await context.bot.unban_chat_member(
            chat_id=group_id,
            user_id=user_id,
            only_if_banned=True,
        )
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
        await update.message.reply_text("⛔ Только администратор группы может зарегистрировать её.")
        return

    db = load_db()
    if "registered_groups" not in db:
        db["registered_groups"] = []

    if chat_id in db["registered_groups"]:
        await update.message.reply_text("ℹ️ Эта группа уже зарегистрирована.")
        return

    db["registered_groups"].append(chat_id)
    save_db(db)
    await update.message.reply_text("✅ Группа успешно зарегистрирована! Бот готов к работе.")

# -------------------------------------------------------------------
# Вспомогательные функции для магазина – выбор группы
# -------------------------------------------------------------------
async def get_user_groups(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> List[int]:
    """Возвращает список ID зарегистрированных групп, в которых состоит пользователь."""
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

async def choose_group(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Optional[int]:
    """Предлагает пользователю выбрать группу (если больше одной) и возвращает group_id или None."""
    groups = await get_user_groups(user_id, context)
    if not groups:
        await update.message.reply_text("❌ Вы не состоите ни в одной зарегистрированной группе.")
        return None
    if len(groups) == 1:
        return groups[0]

    # Если несколько групп, показываем кнопки выбора
    keyboard = []
    for gid in groups:
        try:
            chat = await context.bot.get_chat(gid)
            name = chat.title or f"ID {gid}"
        except:
            name = f"ID {gid}"
        keyboard.append([InlineKeyboardButton(name, callback_data=f"select_group_{gid}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="main_menu")])
    await update.message.reply_text(
        "Выберите группу, в которой хотите совершить покупку:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return None  # ждём коллбэк

async def get_group_name(context, gid: int) -> str:
    try:
        chat = await context.bot.get_chat(gid)
        return chat.title or f"ID {gid}"
    except:
        return f"ID {gid}"

# -------------------------------------------------------------------
# Команды администраторов (в группах, бесплатные)
# -------------------------------------------------------------------
async def cmd_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not await is_group_registered(group_id):
        await update.message.reply_text("❌ Группа не зарегистрирована. Используйте /register.")
        return
    if not await is_user_admin(update.effective_user.id, context, group_id):
        await update.message.reply_text("⛔ Только для администраторов.")
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
        await update.message.reply_text(f"Ошибка получения администраторов: {e}")
        return

    if not member:
        await update.message.reply_text("❌ Пользователь не найден среди администраторов.")
        return

    try:
        await give_prefix(context, group_id, member.id, title)
        await update.message.reply_text(f"🏷️ Префикс '{title}' выдан пользователю @{username}.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not await is_group_registered(group_id):
        await update.message.reply_text("❌ Группа не зарегистрирована. Используйте /register.")
        return
    if not await is_user_admin(update.effective_user.id, context, group_id):
        await update.message.reply_text("⛔ Только для администраторов.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /mute @username <10m|1h|forever>")
        return
    target_text = context.args[0]
    dur_str = context.args[1].lower()
    uid = await resolve_user(target_text, context, group_id)
    if not uid:
        await update.message.reply_text("❌ Пользователь не найден в группе.")
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
    db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == uid and m["group_id"] == group_id)]
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
        await update.message.reply_text("❌ Группа не зарегистрирована. Используйте /register.")
        return
    if not await is_user_admin(update.effective_user.id, context, group_id):
        await update.message.reply_text("⛔ Только для администраторов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /unmute @username")
        return
    uid = await resolve_user(context.args[0], context, group_id)
    if not uid:
        await update.message.reply_text("❌ Пользователь не найден в группе.")
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
    db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == uid and m["group_id"] == group_id)]
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
# Админ-панель (выбор группы)
# -------------------------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    groups = await get_user_groups(user_id, context)
    if not groups:
        await update.message.reply_text("❌ Вы не состоите ни в одной зарегистрированной группе.")
        return
    if len(groups) == 1:
        group_id = groups[0]
        await show_admin_panel(update, context, group_id)
    else:
        keyboard = []
        for gid in groups:
            name = await get_group_name(context, gid)
            keyboard.append([InlineKeyboardButton(name, callback_data=f"admin_group_{gid}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="main_menu")])
        await update.message.reply_text(
            "Выберите группу для просмотра панели администратора:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: int):
    db = load_db()
    text = f"🛡️ *Панель администратора*\nГруппа: {await get_group_name(context, group_id)}\n\n"

    prefixes = [p for p in db.get("prefixes", []) if p.get("group_id") == group_id]
    text += "*Активные префиксы:*\n"
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

    mutes = [m for m in db.get("mutes", []) if m.get("group_id") == group_id]
    now_ts = datetime.now(timezone.utc).timestamp()
    active = [m for m in mutes if m.get("until_date") is None or m["until_date"] > now_ts]
    text += "\n*Текущие муты:*\n"
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

    history = [h for h in db.get("history", []) if h.get("group_id") == group_id]
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

    await update.message.reply_text(text, parse_mode="Markdown")

# -------------------------------------------------------------------
# Магазин (в личных сообщениях)
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
    # профиль показывает данные по всем группам
    text = "👤 *Ваш профиль*\n\n"
    groups = await get_user_groups(user_id, context)
    if not groups:
        text += "Вы не состоите в зарегистрированных группах."
    for gid in groups:
        gname = await get_group_name(context, gid)
        text += f"\n📁 *{gname}:*\n"
        prefix = next((p for p in db.get("prefixes", []) if p["user_id"] == user_id and p.get("group_id") == gid), None)
        if prefix:
            if prefix.get("expires_at"):
                exp = datetime.fromtimestamp(prefix["expires_at"], tz=timezone.utc)
                remaining = exp - datetime.now(timezone.utc)
                if remaining.total_seconds() > 0:
                    text += f"  🏷️ Префикс: {prefix['title']} (истекает через {str(remaining).split('.')[0]})\n"
                else:
                    text += "  🏷️ Префикс: истёк\n"
            else:
                text += f"  🏷️ Префикс: {prefix['title']} (навсегда)\n"
        else:
            text += "  🏷️ Нет активного префикса\n"

        mutes = [m for m in db.get("mutes", []) if m["muter_id"] == user_id and m.get("group_id") == gid]
        if mutes:
            text += "  🔇 Купленные муты:\n"
            for m in mutes:
                try:
                    target = await context.bot.get_chat(m["target_id"])
                    tname = f"@{target.username}" if target.username else target.first_name
                except:
                    tname = f"ID {m['target_id']}"
                if m.get("until_date"):
                    if m["until_date"] > datetime.now(timezone.utc).timestamp():
                        exp = datetime.fromtimestamp(m["until_date"], tz=timezone.utc).strftime("%H:%M")
                        text += f"    → {tname}: до {exp}\n"
                    else:
                        text += f"    → {tname}: истёк\n"
                else:
                    text += f"    → {tname}: Навсегда\n"
        else:
            text += "  🔇 Нет купленных мутов\n"

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

async def start_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, product_type: str):
    """Общий обработчик начала покупки: выбор группы, затем показ карточки товара."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    groups = await get_user_groups(user_id, context)
    if not groups:
        await query.edit_message_text("❌ Вы не состоите ни в одной зарегистрированной группе.")
        return
    if len(groups) == 1:
        group_id = groups[0]
        context.user_data["purchase_group_id"] = group_id
        if product_type == "prefix":
            await show_prefix_card(update, context)
        elif product_type == "mute":
            await show_mute_card(update, context)
        elif product_type == "unmute":
            await show_unmute_card(update, context)
    else:
        keyboard = []
        for gid in groups:
            name = await get_group_name(context, gid)
            keyboard.append([InlineKeyboardButton(name, callback_data=f"pick_{product_type}_{gid}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="shop")])
        await query.edit_message_text(
            "Выберите группу для покупки:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def show_prefix_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🏷️ *Префикс*\n"
        "────────────────\n"
        "Получите зелёный custom title в группе.\n"
        "────────────────\n"
        "Цены:\n"
        "10 мин — 50 ⭐\n1 час — 80 ⭐\n5 часов — 150 ⭐\n"
        "10 часов — 250 ⭐\n24 часа — 350 ⭐\nНавсегда — 400 ⭐\n"
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
        "10 мин — 50 ⭐\n1 час — 100 ⭐\n5 часов — 200 ⭐\n"
        "10 часов — 250 ⭐\n24 часа — 300 ⭐\nНавсегда — 1000 ⭐\n"
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

async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    group_id = context.user_data.get("purchase_group_id")
    if not group_id:
        await update.message.reply_text("Сначала выберите группу в магазине.")
        return

    user_data = context.user_data
    msg_text = update.message.text.strip()

    if "pending_mute" in user_data:
        dur = user_data.pop("pending_mute")
        target_id = await resolve_user(msg_text, context, group_id)
        if not target_id:
            await update.message.reply_text("❌ Пользователь не найден в группе.")
            return
        db = load_db()
        if any(m["target_id"] == target_id and m.get("group_id") == group_id for m in db.get("mutes", [])):
            await update.message.reply_text("🔇 Этот пользователь уже замучен.")
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
            await update.message.reply_text("❌ Пользователь не найден в группе.")
            return
        db = load_db()
        if not any(m["target_id"] == target_id and m.get("group_id") == group_id for m in db.get("mutes", [])):
            await update.message.reply_text("🔊 Этот пользователь не замучен ботом.")
            return
        await update.message.reply_invoice(
            title="Размут",
            description="Снять мут с пользователя",
            payload=f"unmute_{target_id}_{group_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Размут", UNMUTE_PRICE)],
            start_parameter="unmute",
        )

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
        group_id = context.user_data.get("purchase_group_id")
        if not group_id:
            await update.message.reply_text("Ошибка: не выбрана группа.")
            return
        title = "🟢 Premium"
        expires_in = None if dur == "forever" else DURATION_SECONDS[dur]

        try:
            await give_prefix(context, group_id, buyer.id, title)
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось выдать префикс: {e}")
            return

        db = load_db()
        expires_at = None
        if expires_in:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).timestamp()
        db["prefixes"].append({
            "user_id": buyer.id,
            "group_id": group_id,
            "title": title,
            "expires_at": expires_at,
            "purchase_id": payload,
        })
        save_db(db)
        await add_to_history(context, buyer.id, group_id, "Префикс", f"на {DURATION_LABELS[dur]}")

        if expires_in:
            async def demote():
                await remove_prefix(context, group_id, buyer.id)
                db2 = load_db()
                db2["prefixes"] = [p for p in db2["prefixes"] if not (p["user_id"] == buyer.id and p["title"] == title and p.get("group_id") == group_id)]
                save_db(db2)
            asyncio.create_task(delayed_task(expires_in, demote))

        await notify_admins(context, group_id, f"🟢 {buyer_name} ({buyer_mention}) купил префикс на {DURATION_LABELS[dur]}.")
        await context.bot.send_message(
            group_id,
            f"🎉 {buyer_mention} приобрёл зелёный префикс на {DURATION_LABELS[dur]}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Сделать так же", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

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
            delay = until_db - datetime.now(timezone.utc).timestamp()
            if delay > 0:
                asyncio.create_task(delayed_task(delay, cleanup))

        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = f"@{target_user.username}" if target_user.username else target_user.first_name
        except:
            target_name = f"ID {target_id}"
        await add_to_history(context, buyer.id, group_id, "Мут", f"{target_name} на {DURATION_LABELS[dur]}")
        await notify_admins(context, group_id, f"🔇 {buyer_name} ({buyer_mention}) замутил {target_name} на {DURATION_LABELS[dur]}.")
        await context.bot.send_message(
            group_id,
            f"🔇 {buyer_mention} замутил {target_name} на {DURATION_LABELS[dur]}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Сделать так же", url=f"https://t.me/{context.bot.username}?start=start")
            ]]),
        )

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
        await notify_admins(context, group_id, f"🔊 {buyer_name} ({buyer_mention}) размутил {target_name}.")
        await context.bot.send_message(
            group_id,
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
    now = datetime.now(timezone.utc).timestamp()

    for p in db.get("prefixes", []):
        if p.get("expires_at") and p.get("group_id"):
            delay = p["expires_at"] - now
            if delay > 0:
                async def demote_restored(uid=p["user_id"], gid=p["group_id"], title=p["title"]):
                    try:
                        await remove_prefix(app.bot, gid, uid)
                    except Exception as e:
                        logging.error(f"Restore demote error: {e}")
                    db2 = load_db()
                    db2["prefixes"] = [x for x in db2["prefixes"] if not (x["user_id"] == uid and x["title"] == title and x.get("group_id") == gid)]
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
    elif data.startswith("shop_"):
        # Обработка нажатий "Префикс", "Мут", "Размут" в магазине
        product = data.split("_")[1]
        await start_purchase(update, context, product)
    elif data.startswith("pick_"):
        # Выбор группы для покупки
        parts = data.split("_")
        product = parts[1]
        group_id = int(parts[2])
        context.user_data["purchase_group_id"] = group_id
        if product == "prefix":
            await show_prefix_card(update, context)
        elif product == "mute":
            await show_mute_card(update, context)
        elif product == "unmute":
            await show_unmute_card(update, context)
    elif data.startswith("prefix_dur_"):
        await handle_prefix_duration(update, context)
    elif data.startswith("mute_dur_"):
        await handle_mute_duration(update, context)
    elif data.startswith("admin_group_"):
        group_id = int(data.split("_")[2])
        await show_admin_panel(update, context, group_id)
    elif data.startswith("select_group_"):
        group_id = int(data.split("_")[2])
        context.user_data["purchase_group_id"] = group_id
        # если был начат продукт, покажем его карточку
        # но так как мы не знаем, какой продукт, просто вернёмся в магазин
        await show_shop(update, context)
    else:
        await query.answer("Неизвестная команда.")

# -------------------------------------------------------------------
# Запуск
# -------------------------------------------------------------------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    app = Application.builder().token(config.BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("prefix", cmd_prefix))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))

    # Обработчики
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
