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

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Наборы прав для мута/размута
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

# Цены и длительности
PREFIX_PRICES = {
    "10min": 49, "1hour": 149, "5hours": 349,
    "10hours": 649, "24hours": 879, "forever": 1499,
}
MUTE_PRICES = {
    "10min": 49, "1hour": 149, "5hours": 449,
    "10hours": 849, "24hours": 999, "forever": 1999,
}
UNMUTE_PRICE = 99

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

async def cache_username_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    db = load_db()
    if update.effective_user.username:
        if "username_cache" not in db:
            db["username_cache"] = {}
        db["username_cache"][update.effective_user.username.lower()] = update.effective_user.id

    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        if "user_groups_cache" not in db:
            db["user_groups_cache"] = {}
        uid_str = str(update.effective_user.id)
        if uid_str not in db["user_groups_cache"]:
            db["user_groups_cache"][uid_str] = []
        if chat.id not in db["user_groups_cache"][uid_str]:
            db["user_groups_cache"][uid_str].append(chat.id)
    save_db(db)

async def resolve_user(text: str, context: ContextTypes.DEFAULT_TYPE, group_id: int) -> Optional[int]:
    text = text.strip()
    if text.isdigit():
        return int(text)
    username = text.lstrip('@').lower()
    if not username:
        return None
    db = load_db()
    if "username_cache" in db and username in db["username_cache"]:
        return db["username_cache"][username]
    try:
        chat = await context.bot.get_chat(f"@{username}")
        return chat.id
    except:
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
    cached_groups = db.get("user_groups_cache", {}).get(str(user_id), [])
    
    for gid in registered:
        if gid in cached_groups:
            user_groups.append(gid)
            continue
        try:
            member = await context.bot.get_chat_member(gid, user_id)
            if member.status in ("member", "administrator", "creator", "restricted"):
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

# -------------------------------------------------------------------
# Логика Префиксов
# -------------------------------------------------------------------
async def give_prefix(context, group_id: int, user_id: int, title: str) -> bool:
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
    except Exception as e:
        raise Exception(f"Не удалось получить информацию об участнике: {e}")

    if member.status == "creator":
        raise Exception("Невозможно установить префикс владельцу (создателю) группы.")

    # Если пользователь не админ, повышаем его с минимально возможным правом
    if member.status not in ("administrator", "creator"):
        try:
            await context.bot.promote_chat_member(
                chat_id=group_id,
                user_id=user_id,
                can_manage_chat=True,  
                can_change_info=False,
                can_delete_messages=False,
                can_invite_users=False,
                can_restrict_members=False,
                can_pin_messages=False,
                can_promote_members=False,
                can_manage_video_chats=False,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
            )
            # Даем Telegram 1 секунду на синхронизацию серверов перед установкой титула
            await asyncio.sleep(1.0)
        except Exception as e:
            raise Exception(f"Не удалось выдать права администратора бота: {e}")

    # Пытаемся установить титул
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
    # Шаг 1: Сначала принудительно очищаем префикс (пока пользователь еще администратор)
    try:
        await context.bot.set_chat_administrator_custom_title(
            chat_id=group_id,
            user_id=user_id,
            custom_title=""  # Пустая строка убирает префикс
        )
        await asyncio.sleep(0.5) # Даем серверу время обработать очистку
    except Exception as e:
        logging.error(f"Clear title error: {e}")

    # Шаг 2: Затем полностью забираем права администратора
    try:
        await context.bot.promote_chat_member(
            chat_id=group_id, user_id=user_id,
            can_manage_chat=False,
            can_change_info=False, can_delete_messages=False,
            can_invite_users=False, can_restrict_members=False,
            can_pin_messages=False, can_promote_members=False,
            can_manage_video_chats=False,
        )
    except Exception as e:
        logging.error(f"Demote error: {e}")

# -------------------------------------------------------------------
# Админ-команды в группе
# -------------------------------------------------------------------
async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эту команду можно использовать только в группе.")
        return
    if not await is_user_admin(update.effective_user.id, context, chat_id):
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
    target_id = await resolve_user(username, context, group_id)

    if not target_id:
        await update.message.reply_text("❌ Пользователь не найден. Убедитесь, что он писал в чат.")
        return

    try:
        await give_prefix(context, group_id, target_id, title)
        await update.message.reply_text(f"🏷️ Префикс '{title}' успешно выдан @{username}.")
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
        until_date, until_db, label = None, None, "навсегда"
    else:
        try:
            if dur_str.endswith("m"): seconds = int(dur_str[:-1]) * 60
            elif dur_str.endswith("h"): seconds = int(dur_str[:-1]) * 3600
            else: raise ValueError
            until_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            until_date, until_db, label = int(until_dt.timestamp()), until_dt.timestamp(), dur_str
        except:
            await update.message.reply_text("Неверный формат. Примеры: 10m, 1h, forever")
            return

    try:
        await context.bot.restrict_chat_member(chat_id=group_id, user_id=uid, permissions=MUTE_PERMISSIONS, until_date=until_date)
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось замутить: {e}")
        return

    db = load_db()
    if "mutes" not in db: db["mutes"] = []
    db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == uid and m.get("group_id") == group_id)]
    db["mutes"].append({"target_id": uid, "muter_id": update.effective_user.id, "group_id": group_id, "duration": "custom", "until_date": until_db})
    save_db(db)

    try:
        user = await context.bot.get_chat(uid)
        name = f"@{user.username}" if user.username else user.first_name
    except: name = f"ID {uid}"
    bot_username = context.bot.username
    await context.bot.send_message(group_id, f"🔇 {name} получил мут на {label}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Сделать так же 🤡", url=f"https://t.me/{bot_username}?start=start")]]))

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    if not await is_group_registered(group_id): return
    if not await is_user_admin(update.effective_user.id, context, group_id): return
    if not context.args: return
    uid = await resolve_user(context.args[0], context, group_id)
    if not uid: return

    try:
        await context.bot.restrict_chat_member(chat_id=group_id, user_id=uid, permissions=UNMUTE_PERMISSIONS, until_date=0)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    db = load_db()
    if "mutes" in db:
        db["mutes"] = [m for m in db["mutes"] if not (m["target_id"] == uid and m.get("group_id") == group_id)]
        save_db(db)
    await update.message.reply_text("🔊 Мут снят.")

# -------------------------------------------------------------------
# Личный кабинет и навигация
# -------------------------------------------------------------------
async def show_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    groups = await get_user_groups(user_id, context)
    if not groups:
        text = (
            "❌ Вы не состоите ни в одной зарегистрированной группе.\n\n"
            "💡 *Как это исправить?*\nОтправьте любое текстовое сообщение в вашу группу, чтобы бот запомнил вас, а затем вернитесь и напишите /start."
        )
        if update.callback_query: await update.callback_query.edit_message_text(text, parse_mode="Markdown")
        else: await update.message.reply_text(text, parse_mode="Markdown")
        return

    keyboard = [[InlineKeyboardButton(await get_group_name(context, gid), callback_data=f"select_group_{gid}")] for gid in groups]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_selection")])
    text = "Выберите группу для взаимодействия:"
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await cache_username_handler(update, context)
        await update.message.reply_text("Перейдите в личные сообщения с ботом для открытия магазина.")
        return
    await show_group_selection(update, context)

async def select_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_selection":
        await query.edit_message_text("Действие отменено.")
        return
    gid = int(query.data.split("_")[2])
    context.user_data["selected_group"] = gid
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🛒 Магазин", callback_data="shop")],
        [InlineKeyboardButton("🔄 Сменить группу", callback_data="change_group")],
    ]
    text = f"🎛️ *Главное меню*\nВыбранный чат: {await get_group_name(context, context.user_data.get('selected_group'))}"
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# -------------------------------------------------------------------
# Магазин и Профиль
# -------------------------------------------------------------------
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    group_id = context.user_data.get("selected_group")
    if not group_id: return

    db = load_db()
    prefix = next((p for p in db.get("prefixes", []) if p["user_id"] == user_id and p.get("group_id") == group_id), None)
    text = f"👤 *Профиль*\nГруппа: {await get_group_name(context, group_id)}\n\n"
    text += f"🏷️ Префикс: {prefix['title']}\n" if prefix else "🏷️ Нет активного префикса\n"
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]), parse_mode="Markdown")

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
    context.user_data["buy_product"] = "prefix"
    text = "🏷️ *Префикс*\nВыберите длительность аренды:"
    keyboard = [
        [InlineKeyboardButton("10 мин", callback_data="prefix_dur_10min"), InlineKeyboardButton("1 час", callback_data="prefix_dur_1hour")],
        [InlineKeyboardButton("5 часов", callback_data="prefix_dur_5hours"), InlineKeyboardButton("10 часов", callback_data="prefix_dur_10hours")],
        [InlineKeyboardButton("24 часа", callback_data="prefix_dur_24hours"), InlineKeyboardButton("Навсегда", callback_data="prefix_dur_forever")],
        [InlineKeyboardButton("🔙 Назад", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def buy_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["buy_product"] = "mute"
    text = "🔇 *Мут*\nВыберите длительность:"
    keyboard = [
        [InlineKeyboardButton("10 мин", callback_data="mute_dur_10min"), InlineKeyboardButton("1 час", callback_data="mute_dur_1hour")],
        [InlineKeyboardButton("5 часов", callback_data="mute_dur_5hours"), InlineKeyboardButton("10 часов", callback_data="mute_dur_10hours")],
        [InlineKeyboardButton("24 часа", callback_data="mute_dur_24hours"), InlineKeyboardButton("Навсегда", callback_data="mute_dur_forever")],
        [InlineKeyboardButton("🔙 Назад", callback_data="shop")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def buy_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["buy_product"] = "unmute"
    context.user_data["pending_unmute"] = True
    await query.edit_message_text("🔊 *Размут*\nЦена: 70⭐\nОтправьте @username или ID для снятия мута:", parse_mode="Markdown")

# -------------------------------------------------------------------
# Выбор длительности и запуск ожидания ввода текста
# -------------------------------------------------------------------
async def handle_duration_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    product = context.user_data.get("buy_product")
    if not product: return

    if product == "prefix" and data.startswith("prefix_dur_"):
        dur = data.split("_")[2]
        context.user_data["duration"] = dur
        context.user_data["pending_prefix_text"] = True  
        await query.edit_message_text(
            f"✍️ *Введите текст для вашего префикса (макс. 16 символов):*\n"
            f"Выбранная длительность: {DURATION_LABELS[dur]}",
            parse_mode="Markdown"
        )
    elif product == "mute" and data.startswith("mute_dur_"):
        dur = data.split("_")[2]
        context.user_data["duration"] = dur
        context.user_data["pending_mute"] = True
        await query.edit_message_text("Отправьте @username или ID пользователя для мута:")

# -------------------------------------------------------------------
# Обработка текстового ввода (Префиксы и Муты)
# -------------------------------------------------------------------
async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private": return
    group_id = context.user_data.get("selected_group")
    if not group_id:
        await update.message.reply_text("Сначала выберите группу через /start.")
        return

    user_data = context.user_data
    msg_text = update.message.text.strip()

    if "pending_prefix_text" in user_data:
        if len(msg_text) > 16:
            await update.message.reply_text("❌ Текст слишком длинный! Префикс в Telegram не может превышать 16 символов. Введите другой вариант:")
            return
        
        del user_data["pending_prefix_text"]
        dur = user_data.get("duration")
        user_data["custom_prefix_title"] = msg_text  

        amount = PREFIX_PRICES[dur]
        await update.message.reply_invoice(
            title="Покупка префикса",
            description=f"Префикс [{msg_text}] на {DURATION_LABELS[dur]}",
            payload=f"prefix_{dur}_{group_id}",  
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Префикс", amount)],
            start_parameter="prefix",
        )

    elif "pending_mute" in user_data:
        dur = user_data.pop("duration", None)
        del user_data["pending_mute"]
        target_id = await resolve_user(msg_text, context, group_id)
        if not target_id:
            await update.message.reply_text("❌ Пользователь не найден.")
            return
        amount = MUTE_PRICES[dur]
        await update.message.reply_invoice(
            title="Покупка мута",
            description=f"Мут на {DURATION_LABELS[dur]}",
            payload=f"mute_{dur}_{target_id}_{group_id}",
            provider_token="", currency="XTR",
            prices=[LabeledPrice("Мут", amount)],
            start_parameter="mute",
        )

    elif "pending_unmute" in user_data:
        del user_data["pending_unmute"]
        target_id = await resolve_user(msg_text, context, group_id)
        if not target_id: return
        await update.message.reply_invoice(
            title="Размут", description="Снять мут",
            payload=f"unmute_{target_id}_{group_id}",
            provider_token="", currency="XTR",
            prices=[LabeledPrice("Размут", UNMUTE_PRICE)],
            start_parameter="unmute",
        )

# -------------------------------------------------------------------
# Платежи
# -------------------------------------------------------------------
async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    buyer = update.effective_user
    buyer_mention = f"@{buyer.username}" if buyer.username else buyer.full_name
    bot_username = context.bot.username
    do_same_button = InlineKeyboardMarkup([[InlineKeyboardButton("Сделать так же 🤡", url=f"https://t.me/{bot_username}?start=start")]])

    if payload.startswith("prefix_"):
        parts = payload.split("_")
        dur = parts[1]
        group_id = int(parts[2])
        
        title = context.user_data.pop("custom_prefix_title", "Premium")
        expires_in = None if dur == "forever" else DURATION_SECONDS[dur]

        try:
            # Выдаем права и вешаем префикс
            await give_prefix(context, group_id, buyer.id, title)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка выдачи: {e}\nДеньги списаны, но выдать не получилось. Обратитесь к админу.")
            return

        db = load_db()
        if "prefixes" not in db: db["prefixes"] = []
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).timestamp() if expires_in else None
        db["prefixes"].append({"user_id": buyer.id, "group_id": group_id, "title": title, "expires_at": expires_at})
        save_db(db)

        await update.message.reply_text("✅ Платёж успешен! Префикс успешно установлен в группе.")

        if expires_in:
            async def demote():
                await remove_prefix(context, group_id, buyer.id)
                db2 = load_db()
                db2["prefixes"] = [p for p in db2.get("prefixes", []) if not (p["user_id"] == buyer.id and p.get("group_id") == group_id)]
                save_db(db2)
            asyncio.create_task(delayed_task(expires_in, demote))

        await context.bot.send_message(group_id, f"🎉 {buyer_mention} приобрёл кастомный префикс «*{title}*» на {DURATION_LABELS[dur]}.", reply_markup=do_same_button, parse_mode="Markdown")

    elif payload.startswith("mute_"):
        parts = payload.split("_")
        dur, target_id, group_id = parts[1], int(parts[2]), int(parts[3])
        until_date = int((datetime.now(timezone.utc) + timedelta(seconds=DURATION_SECONDS[dur])).timestamp()) if dur != "forever" else None
        
        try:
            await context.bot.restrict_chat_member(chat_id=group_id, user_id=target_id, permissions=MUTE_PERMISSIONS, until_date=until_date)
        except: return

        await update.message.reply_text("✅ Платёж успешен! Пользователь замучен.")

        try:
            t_user = await context.bot.get_chat(target_id)
            t_name = f"@{t_user.username}" if t_user.username else t_user.first_name
        except: t_name = f"ID {target_id}"

        await context.bot.send_message(group_id, f"🔇 {buyer_mention} замутил {t_name} на {DURATION_LABELS[dur]}.", reply_markup=do_same_button)

# -------------------------------------------------------------------
# Инициализация
# -------------------------------------------------------------------
def main():
    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("register", cmd_register))
    application.add_handler(CommandHandler("prefix", cmd_prefix))
    application.add_handler(CommandHandler("mute", cmd_mute))
    application.add_handler(CommandHandler("unmute", cmd_unmute))

    application.add_handler(CallbackQueryHandler(select_group_callback, pattern="^select_group_|^cancel_selection$"))
    application.add_handler(CallbackQueryHandler(profile, pattern="^profile$"))
    application.add_handler(CallbackQueryHandler(shop, pattern="^shop$"))
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(buy_prefix, pattern="^buy_prefix$"))
    application.add_handler(CallbackQueryHandler(buy_mute, pattern="^buy_mute$"))
    application.add_handler(CallbackQueryHandler(buy_unmute, pattern="^buy_unmute$"))
    application.add_handler(CallbackQueryHandler(show_group_selection, pattern="^change_group$"))
    application.add_handler(CallbackQueryHandler(handle_duration_selection, pattern="^(prefix_dur_|mute_dur_)"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_target_input))

    application.add_handler(PreCheckoutQueryHandler(precheckout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    application.add_handler(MessageHandler(filters.ALL, cache_username_handler), group=0)

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
