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
# ChatPermissions (совместимо с любой версией библиотеки)
# -------------------------------------------------------------------
def get_mute_permissions():
    params = {
        'can_send_messages': False,
        'can_send_other_messages': False,
        'can_add_web_page_previews': False,
        'can_change_info': False,
        'can_invite_users': False,
        'can_pin_messages': False,
    }
    if hasattr(ChatPermissions, 'can_send_polls'):
        params['can_send_polls'] = False
    if hasattr(ChatPermissions, 'can_send_media_messages'):
        params['can_send_media_messages'] = False
    return ChatPermissions(**params)

def get_unmute_permissions():
    params = {
        'can_send_messages': True,
        'can_send_other_messages': True,
        'can_add_web_page_previews': True,
        'can_change_info': False,
        'can_invite_users': True,
        'can_pin_messages': False,
    }
    if hasattr(ChatPermissions, 'can_send_polls'):
        params['can_send_polls'] = True
    if hasattr(ChatPermissions, 'can_send_media_messages'):
        params['can_send_media_messages'] = True
    return ChatPermissions(**params)

# -------------------------------------------------------------------
# Поиск пользователя – надёжный метод
# -------------------------------------------------------------------
async def resolve_user(text: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """
    Ищет пользователя по @username или числовому ID.
    Использует get_chat_member с @username – самый надёжный способ.
    """
    text = text.strip()

    # 1) Числовой ID (только положительные числа)
    if text.isdigit():
        user_id = int(text)
        try:
            await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
            return user_id
        except Exception:
            pass

    # 2) Username (с @ или без)
    username = text.lstrip('@')
    if username:
        # Пробуем прямой вызов get_chat_member с @username
        try:
            member = await context.bot.get_chat_member(GROUP_CHAT_ID, "@" + username)
            return member.user.id
        except Exception:
            pass

        # Запасной вариант: получаем ID через get_chat
        try:
            user = await context.bot.get_chat("@" + username)
            if user.type == 'private':
                # Проверяем членство
                await context.bot.get_chat_member(GROUP_CHAT_ID, user.id)
                return user.id
        except Exception:
            pass

    return None

# -------------------------------------------------------------------
# Проверка прав администратора
# -------------------------------------------------------------------
async def is_user_admin(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        member = await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return member.status in ("creator", "administrator")
    except:
        return False

# -------------------------------------------------------------------
# Вспомогательные функции
# -------------------------------------------------------------------
async def delayed_task(delay: float, coro):
    await asyncio.sleep(delay)
    await coro()

async def add_to_history(context: ContextTypes.DEFAULT_TYPE, buyer_id: int, product: str, details: str):
    db = load_db()
    if "history" not in db:
        db["history"] = []
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
# Команды администраторов (бесплатные мут/размут)
# -------------------------------------------------------------------
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_admin(user_id, context):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Использование: /mute @username <длительность>\n"
            "Примеры: 10m (минуты), 1h (часы), forever"
        )
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
            await update.message.reply_text("Неверный формат длительности. Используйте: Xm, Xh или forever.")
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
        await update.message.reply_text(f"❌ Не удалось замутить: {e}")
        return

    # Сохраняем в БД
    db = load_db()
    if "mutes" not in db:
        db["mutes"] = []
    db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
    db["mutes"].append({
        "target_id": target_id,
        "muter_id": user_id,
        "duration": "custom",
        "until_date": until_db,
    })
    save_db(db)

    # Планируем очистку записи после истечения
    if until_db:
        async def cleanup():
            db = load_db()
            if "mutes" in db:
                db["mutes"] = [m for m in db["mutes"]
                               if not (m["target_id"] == target_id and m["until_date"] == until_db)]
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
    await context.bot.send_message(GROUP_CHAT_ID, f"🔇 {target_name} получил мут на {label}.")

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    db = load_db()
    if "mutes" in db:
        db["mutes"] = [m for m in db["mutes"] if m["target_id"] != target_id]
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
    prefixes = db.get("prefixes", [])
    text += "*Активные префиксы:*\n"
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
    mutes = db.get("mutes", [])
    now_ts = datetime.utcnow().timestamp()
    active_mutes = [m for m in mutes if m.get("until_date") is None or m["until_date"] > now_ts]
    text += "\n*Текущие муты:*\n"
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
# Магазин (интерфейс)
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
        reply_markuparkup=Inline=InlineKeyboardMarkKeyboardMarkup(keyup(keyboardboard),
        parse),
        parse_mode="_mode="MarkdownMarkdown",
   ",
    )

# )

# Карточ Карточки товаки товаровров
async def
async def show_prefix show_prefix_card(_card(update: Update, context:update: Update, context: ContextTypes ContextTypes.DEFAULT.DEFAULT_TYPE_TYPE):
    query):
    query = update = update.callback.callback_query_query
    await
    await query. query.answeranswer()
    text()
    text = = (
        " (
        "🏷🏷️ *️ *ПрефиПрефикс*\nкс*\n"
        ""
        "────────────────\────────────────\nn"
"
        "        "Получите зПолучиелёте зный customелё title вный custom группе.\n title в группе"
       .\n"
        "────────────────\ "────────────────\nn"
        "Ц"
        "Цены:\ены:\n"
n"
        "        "10 мин10 мин —  — 5050 ⭐\ ⭐\n"
        "n"
        "1 час — 1 час80 — 80 ⭐\n ⭐\"
        "n5"
        " часов —5 часов  — 150150 ⭐\ ⭐\nn"
        ""
        "10 часов10 часов —  — 250250 ⭐\ ⭐\nn"
        ""
        "24 часа24 часа —  — 350350 ⭐\ ⭐\nn"
        "Нав"
        "Навсегдасегда — 400 — 400 ⭐\ ⭐\nn"
        ""
        "ВыбеВыберите длирите длительностьтельность::"
   "
    )
    keyboard = )
    keyboard = [
        [Inline [
        [InlineKeyboardButtonKeyboardButton("10("10 мин · мин · 50 50⭐",⭐", callback_data callback_data="prefix="prefix_dur_dur_10_10min")min")],
       ],
        [Inline [InlineKeyboardButtonKeyboardButton("1("1 час · час · 80 80⭐",⭐", callback_data callback_data="prefix="prefix_dur_dur_1_1hour")hour")],
        [],
        [InlineInlineKeyboardButtonKeyboardButton("5 часов ·("5 часов · 150 150⭐",⭐", callback_data callback_data="prefix="prefix_dur_dur_5_5hours")],
       hours")],
        [InlineKeyboardButton [InlineKeyboardButton("10 часов ·("10 часов · 250⭐", callback_data="prefix 250⭐", callback_data="prefix_d_durur__1010hourshours")")],
],
               [Inline [InlineKeyboardButtonKeyboardButton("24("24 часа · часа · 350 350⭐",⭐", callback_data callback_data="prefix="prefix_dur_dur_24_24hours")hours")],
        [Inline],
        [InlineKeyboardButton("НаKeyboardButtonвсе("Нагда ·все 400гда ·⭐", 400⭐", callback_data="prefix_d callback_data="prefix_durur_fore_forever")ver")],
       ],
        [Inline [InlineKeyboardButton("KeyboardButton🔙 На("зад в🔙 Назад в магазин", callback магазин", callback_data="_data="shop")],
   shop") ]
   ],
    ]
    await query.edit_message await query_text(text.edit_message, reply_text(text_mark, reply_markup=up=InlineKeyboardInlineKeyboardMarkupMarkup(keyboard(keyboard), parse), parse_mode="Markdown_mode="")

asyncMarkdown")

async def handle_prefix_d def handleuration_prefix_d(update:uration( Update,update: Update context:, context ContextTypes: Context.DEFAULTTypes.D_TYPEEFAULT_TYPE):
    query):
    query = update = update.callback.callback_query_query

    await query.    awaitanswer query.()
answer    dur = query()
    dur =.data.split("_")[2 query.data.split("_")[2]
    await]
    await query.edit_message_text query.edit_message_text("("💳 От💳 Отправляправляю сю счёт...чёт...")
   ")
    await context await context.bot.bot.send_in.send_invoicevoice(
        chat(
        chat_id=_id=query.fromquery.from_user.id,
       _user.id,
        title="Покуп title="Покупка прека префикфиксаса",
        description",
        description=f"=f"ЗелЗелёёныйный префи префикс накс на {D {DURATIONURATION_LAB_LABELS[dELS[dur]}ur]}",
       ",
        payload=f payload=f"prefix"prefix_{dur_{dur}}",
        provider_token="",
        provider_token="",
        currency="",
        currency="XTRXTR",
       ",
        prices prices=[Labeled=[LabeledPrice("Price("ПрефиПрефикс",кс", PREFIX PREFIX_PRICES_PRICES[dur[dur])])],
        start],
        start_parameter_parameter="prefix="prefix",
   ",
    )

async def show_mute )

async def show_mute_card(_card(update:update: Update, Update, context: context: ContextTypes ContextTypes.D.DEFAULTEFAULT_TYPE_TYPE):
    query):
    query = update = update.callback.callback_query_query

    await    await query query..answeranswer()
    text()
    text = = (
        " (
        "🔇🔇 *М *Мут*\ут*\nn"
        ""
        "────────────────\────────────────\nn"
        "Заму"
        "Замутить любоготить любого участника участника группы.\n группы.\n"
        ""
        "────────────────\────────────────\nn"
        ""
        "Цены:\Ценыn:\n"
       "
        "10 "10 мин — мин — 50 50 ⭐ ⭐\n\n"
       "
        "1 "1 час — час — 100 ⭐ 100 ⭐\n"
       \n "5"
        "5 часов — 200 часов — ⭐ 200 ⭐\n\n"
       "
        "10 "10 часов — часов — 250 250 ⭐ ⭐\n\n"
"
               "24 "24 часа — часа — 300 300 ⭐ ⭐\n\n"
       "
        "На "Навсевсегда —гда — 100 10000 ⭐\ ⭐\n"
        "nСначала"
        "Сначала выбе выберите длирите длительность:тельность:"
   "
    )
    )
    keyboard = keyboard = [
        [
        [Inline [InlineKeyboardButtonKeyboardButton("("1010 мин · мин · 50 50⭐",⭐", callback_data callback_data="="mmute_dute_dur_10minur_")10min],
       ") [InlineKeyboard],
       Button(" [InlineKeyboardButton("1 час1 час · 100⭐ · 100⭐", callback", callback_data="_data="mutemute_dur__dur_1hour")1hour")],
       ],
        [Inline [InlineKeyboardButtonKeyboardButton("5("5 часов · часов · 200 200⭐",⭐", callback_data="m callback_data="mute_dur_ute_dur_5hours5hours")")],
       ],
        [Inline [InlineKeyboardKeyboardButtonButton("("10 часов10 часов · ·  250⭐250⭐", callback", callback_data="mute_data="mute_d_durur__1010hours")hours")],
       ],
        [ [InlineInlineKeyboardButtonKeyboardButton("24("24 часа · 300 часа ·⭐", 300⭐", callback_data="mute_d callback_data="mute_dur_ur_24hours24hours")")],
       ],
        [InlineKeyboard [InlineKeyboardButton("Button("НавНавсегдасегда ·  · 10001000⭐",⭐", callback_data callback_data="m="mute_dute_dur_ur_foreverforever")")],
       ],
        [InlineKeyboardButton(" [InlineKeyboardButton("🔙🔙 Назад Назад в мага в магазин",зин", callback_data callback_data="shop="shop")")],
   ],
    ]
    await ]
    await query.edit query.edit_message_text_message_text(text, reply_m(text,arkup reply_m=Inlinearkup=InlineKeyboardMarkKeyboardMarkup(keyup(keyboard),board), parse_mode="Markdown")

async def handle_mute_duration(update: parse_mode="Markdown")

async def handle_mute_duration(update: Update, Update, context: context: ContextTypes ContextTypes.DEFAULT.DEFAULT_TYPE_TYPE):
    query):
    query = update = update.callback_query.callback_query
    await
    await query.answer query.answer()
    dur()
    dur = query.data.split = query.data.split("_("_")")[2[2]
    context.user_data]
    context["pending.user_data_mute["pending"] =_mute dur"] = dur

    await    await query.edit_message_text query.edit(
       _message_text(
        "Отправьте "Отправьте @username @username или чис или числовойловой ID ID пользователя пользователя, которого, которого хотите хотите заму замутить.\тить.\nn"
        ""
        "Пример: `@Пример: `@username`username` или ` или `123456123456789`789`",
       ",
        parse_mode="Mark parse_mode="Markdown",
        reply_markdown",
        reply_markup=up=InlineKeyboardInlineKeyboardMarkup([[Markup([[InlineInlineKeyboardButton("KeyboardButton❌ От("❌ Отмена",мена", callback_data callback_data="shop="shop")]]")]]),
    )

),
   async )

async def show def show_unmute_unmute_card(update_card(update: Update: Update, context, context: Context: ContextTypes.DTypes.DEFAULT_TYPEEFAULT_TYPE):
    query = update.call):
    query = update.callback_queryback_query
   
    await query await query.answer.answer()
   ()
    text = (
        text = (
        " "🔊 *🔊 *РазмуРазмут*\т*\nn"
        ""
        "────────────────\────────────────\nn"
        ""
        "Снять мутСнять с пользова мут с пользователя.\теля.\nn"
        ""
        "ЦенаЦена: : 7070 ⭐\ ⭐\nn"
        ""
        "────────────────\n"
        "────────────────\n"
        "ОтправОтправьте @username илиьте @username или ID человека ID человека, которого нужно раз, которого нужно размутитьмутить::"
   "
    )
    context )
    context.user_data.user_data["pending_unm["pending_unmute"]ute"] = True = True
    await query
    await query.edit_message.edit_message_text_text(
        text(
        text,
       ,
        parse_mode parse_mode="Mark="Markdowndown",
        reply",
        reply_mark_markup=up=InlineKeyboardInlineKeyboardMarkupMarkup([[Inline([[InlineKeyboardButtonKeyboardButton("("❌ От❌ Отмена",мена", callback_data callback="shop_data")]]),
="shop")]]   ),
    )

# )

# ----------------------------------------------------------------- -------------------------------------------------------------------
# О--
# Обработбработчик вчик ввода целивода цели для для покуп покупкики
# -----------------------------------------------------------------
# -------------------------------------------------------------------
async--
async def handle def handle_target_input_target_input(update(update: Update: Update, context: Context, context: ContextTypes.DTypes.DEFAULT_TYPEEFAULT_TYPE):
    if update):
   .eff if update.effective_chat.typeective_ch !=at.type != " "privateprivate":
        return":
        return

   

    user_data user_data = context.user_data = context.user_data
    msg_text
    msg_text = update = update.message.text.message.text.strip.strip()

    if "pending()

    if "pending_mute_mute" in" in user_data:
        dur = user_data:
        dur = user_data user_data.pop(".pop("pending_mpending_muteute")
        target_id =")
        target await resolve_id = await resolve_user(msg_user(msg_text,_text, context context)
       )
        if not target if not target_id_id:
            await:
            await update.message update.message.reply_text(".reply❌_text("❌ Пользова Пользователь нетель не найден найден в груп в группепе.")
            return.")
            return

       

        db = db = load_db load_db()
       ()
        if any if any(m["(m["target_idtarget_id"] =="] == target_id target_id for m for m in db.get(" in dbmutes.get("", []mutes", [])):
            await update)):
           .message.re await update.message.reply_text("ply_text("🔇 Этот🔇 Этот пользователь пользователь уже за уже замученмучен.")
           .")
            return return

        amount = M

        amount = MUTE_PRUTE_PRICES[dICES[durur]
        await update.message]
        await update.message.reply.reply_invoice_invoice(
           (
            title="Покуп title="ка муПокупка мутата",
            description",
            description=f"=f"МутМут на { на {DURATION_LABELSDURATION_LABELS[dur[dur]}]}",
            payload",
            payload=f"mute=f"mute_{dur_{dur}_{target}_{target_id}",
           _id} provider",
            provider_token_token="="",
            currency",
            currency="X="XTRTR",
           ",
            prices=[Label prices=[edPriceLabeledPrice("М("Мут",ут", amount)],
            start amount)],
            start_parameter="_parameter="mutemute",
       ",
        )

    elif " )

    elif "pending_unpending_unmutemute" in user_data" in user_data:
       :
        del user del user_data["pending_un_data["pending_unmutemute"]
"]
               target_id = await resolve_user(msg_text, context)
        target_id = await resolve_user(msg_text, context)
        if not target_id if not target_id:
           :
            await update await update.message.re.message.reply_text("ply_text("❌ П❌ Пользователь не найользователь не найден вден в группе группе.")
           .")
            return return

       

        db = load db = load_db()
        if_db()
        if not any not any(m["(m["target_idtarget_id"] == target_id"] == target_id for m for m in db in db.get(".get("mutesmutes", [])):
           ", [])):
            await update await update.message.reply_text.message.re("ply_text("🔊 Этот пользователь🔊 Этот пользователь не заму не замучен ботченом.")
            return ботом

       .")
            return await update

        await update.message.re.message.reply_invoiceply_in(
            titlevoice(
            title="Раз="Размут",
            description="Снять мут с пользователямут",
            description="Снять мут с пользова",
            payload=f"теля",
            payloadunm=f"ute_{unmtarget_idute_{target_id}",
            provider}",
            provider_token="_token="",
",
                       currency=" currency="XTRXTR",
           ",
            prices prices=[Labeled=[LabeledPrice("Price("РазмуРазмут",т", UNM UNMUTE_PRUTE_PRICE)ICE)],
           ],
            start_ start_parameter="unmparameter="uteunmute",
        )

# -------------------------------------------------------------------
#",
        )

# -------------------------------------------------------------------
# Плат Платёжныеёжные обработчи обработчики
# -----------------------------------------------------------------ки
# -------------------------------------------------------------------
async--
async def pre def precheckoutcheckout(update(update: Update: Update, context, context: Context: ContextTypes.DTypes.DEFAULT_TYPEEFAULT_TYPE):
   ):
    query = update.pre query = update.pre_checkout_checkout_query_query
    await query.
    awaitanswer(ok query.answer(=Trueok=True)

async)

async def successful def successful_payment(update_payment(update: Update: Update, context, context: Context: ContextTypes.DTypes.DEFAULT_TYPEEFAULT_TYPE):
   ):
    payment = payment = update.message update.message.successful.successful_payment_payment
   
    payload = payload = payment.in payment.invoice_pvoiceayload_payload
    buyer = update
    buyer.eff = update.effective_user
   ective_user buyer_name
    buyer_name = buyer.full_name = buyer
   .full_name
    buyer_username = buyer_ buyer.usernameusername = buyer.username
    buyer
   _ buyer_mention =mention = f" f"@{buy@{buyer_username}"er_username}" if buyer if buyer_username_username else buyer else buyer_name_name

    await update.message

    await update.message.reply.re_text("✅ Пply_text("латё✅ Пж успешен!латёж успешен! Обрабатыва Обрабатываю...ю...")

    if payload")

    if payload.startswith.startswith("prefix_"("prefix_"):
        dur):
        dur = payload = payload.split(".split("_")_")[1[1]
       ]
        title = title = " "🟢🟢 Premium Premium"
        expires"
        expires_in =_in = None if None if dur == dur == "forever" "forever" else DURATION else DURATION_SECONDS[dur_SECONDS[dur]

       ]

        # Установка # У префикса
        try:
            awaitстановка префикса
        try:
            await context.b context.bot.pot.promoteromote_chat_chat_member_member(
               (
                chat_id chat_id=GROUP=GROUP_CHAT_CHAT_ID_ID,
                user,
                user_id=b_id=buyuyerer.id,
                is.id_anonymous,
                is_anonymous=False=False,
                can_manage,
                can_manage_chat=False_chat,
                can=False,
                can_change_info=False,
_change_info=False                can_delete,
                can_delete_m_messages=Falseessages=False,
                can_in,
                can_invitevite_users=False,
               _users=False,
                can_restrict can_restrict_members=False_members,
               =False,
                can can_pin_pin_messages_messages=False=False,
                can,
                can_promote_m_promote_members=False,
               embers=False can_,
                can_manage_video_chmanage_video_chats=False,
               ats=False can_,
                can_manage_topics=Falsemanage_topics=False,
            )
           ,
            await context )
            await context.bot.set_ch.botat_.set_chat_administrator_customadministrator_title_custom_title(
                chat(
                chat_id=GROUP_CH_id=GROUP_CHAT_ID,
               AT_ID,
                user_id user_id=buy=buyer.id,
               er.id,
 custom_title                custom_title=title,
            )
        except Exception=title,
            )
        except Exception as e as e:
           :
            await update await update.message.re.message.reply_text(f"ply_text(f"❌❌ Не удалось установ Не удалось установить преить префикс: {фикс: {e}")
            returne}")
            return

       

        # Сохра # Сохраняемняем
        db = load
        db = load_db_db()
        if "prefix()
        if "prefixes"es" not in not in db:
            db["prefixes"] = []
        db["prefix db:
            db["prefixes"] = []
        db["prefixes"].es"].appendappend({
            "({
            "useruser_id_id": buyer": buyer.id,
           .id "title":,
            "title": title title,
            ",
            "expiresexpires_at":_at": (datetime (datetime.utcnow.ut() +cnow() + timed timedelta(seconds=expires_inelta(seconds=expires_in)).timestamp)).timestamp() if expires_in() if else None expires_in else None,
           ,
            "purchase_id "purchase_id": payload": payload,
       ,
        })
        save_db })
       (db save_db(db)
        await add_to)
        await add_to_history(context, buyer_history(context, buyer.id,.id, "Пре "Префиксфикс", f", f"на"на {D {DURATIONURATION_LAB_LABELS[dELS[dur]}")

       ur]}")

        # Ав # Автоснятие
        if expires_inтоснятие
        if expires_in:
            async def dem:
            async def demoteote():
                try:
                   ():
                try:
                    await context await context.bot.prom.botote_ch.promote_chat_memberat_m(
                        chatember(
                        chat_id=_id=GROUP_CHAT_ID,
                        user_id=buyer.id,
                        is_anonymous=FalseGROUP_CHAT_ID,
                        user_id=buyer.id,
                        is_,
                       anonymous=False can_,
                        can_manage_chmanage_chat=Falseat=False,
                       ,
                        can_change can_change_info=False_info=False,
                       ,
                        can_delete can_delete_messages_messages=False=False,
                        can,
                        can_inv_invite_usersite_users=False=False,
                        can,
                        can_restrict_m_restrict_members=Falseembers=False,
                       ,
                        can_pin_m can_pin_messages=False,
                       essages=False,
                        can_promote can_promote_members_members=False=False,
                        can_manage_video,
                        can_manage_video_chats_chats=False,
                        can=False,
_manage_topics                        can_manage_topics=False,
                   =False,
                    )
                    db )
                    db2 =2 = load_db load_db()
                   ()
                    if " if "prefixes" inprefixes" in db2:
                        db2:
                        db2["prefixes"] db2["prefix =es"] = [p for p in [p for p in db2 db2["prefix["prefixes"]es"] if not (p["user if not (p["user_id"] == buyer.id and_id"] == buyer.id and p[" p["title"] ==title"] == title title)]
)]
                    save_db                    save_db(db2)
               (db2)
                except Exception as e except Exception:
                    as e:
                    logging.error logging.error(f"(f"ОшибОшибка снятияка снятия префи префикса: {кса: {e}")
            ase}")
            asyncioyncio.create_task(del.create_task(delayed_taskayed_task(exp(expires_inires_in, demote, demote))

        await notify_ad))

        await notify_admins(context, fmins(context, f"🟢"🟢 {buy {buyer_nameer_name} ({} ({buyer_mention}) куbuyer_mention}) купилпил префикс на префикс на {D {DURATIONURATION_LABELS[d_LABELS[dur]}.")
       ur]}.")
        await context.bot.send_message await context.bot.send_message(
            GROUP_CH(
            GROUP_CHAT_ID,
           AT_ID f",
            f"🎉 {buy🎉er_mention} {buyer_ приобmention} приобрёлрёл зелёный зелёный префикс на префикс на {D {DURATION_LABURATION_LABELS[dur]}ELS[dur]}.",
           .",
            reply_mark reply_markupup=Inline=InlineKeyboardMarkKeyboardMarkup([[
               up([[
                InlineKeyboardButton Inline("СKeyboardButton("Сделать так жеделать так же", url", url=f"https://=f"https://t.me/{contextt.me/{context.bot.username}.bot?start.username}?start=start")
           =start")
            ]] ]]),
       ),
        )

    elif )

    elif payload.start payload.startswith("swith("mutemute_"_"):
        _, dur, target_id):
        _, dur, target_id_str =_str = payload.split("_ payload.split("_")
        target_id")
        target_id = int(target = int_id(target_id_str)
        now_str)
        now = datetime = datetime.ut.utcnowcnow()
       ()
        if dur == if dur == " "foreverforever":
            until_date = None":
            until_date = None
            until_db = None
            until_db = None
       
        else else:
            seconds = D:
            seconds = DURATIONURATION_SECONDS_SECONDS[dur[dur]
           ]
            until_date = int until_date((now = int((now + timedelta( + timedelta(seconds=seconds=seconds)).timestampseconds)).timestamp())
            until_db =())
            until_db = until_date

        until_date

        try try:
            await:
            await context.bot.rest context.bot.restrict_chat_mrict_chat_memberember(
                chat(
                chat_id=_id=GROUP_CHGROUP_CHAT_IDAT_ID,
               ,
                user_id=target_id user_id=target_id,
                permissions,
                permissions=get_mute_per=get_mute_permissionsmissions(),
                until(),
                until_date=until_date_date=until_date,
           ,
            )
        except Exception )
        except Exception as e as e:
            await update:
           .message.re await update.message.reply_text(f"❌ Не удалось заply_text(f"❌ Не удалось замутитьмутить: {e: {}")
            returne}")
            return

        db =

        load_db db = load_db()
        if "()
        if "mutesmutes" not" not in db in db:
           :
            db[" db["mutesmutes"] ="] = []
        []
        db[" db["mutesmutes"] ="] = [m [m for m for m in db["m in db["mutes"]utes"] if m["target if m["target_id"] != target_id"]_id]
        db != target_id["m]
        db["mutes"].utes"].appendappend({
            "({
            "target_idtarget_id": target": target_id_id,
           ,
            " "muter_id":muter_id": buyer.id,
            buyer.id,
            "duration": dur "duration,
           ": dur,
            "until_date": "until_date": until until_db_db,
       ,
        })
        })
        save_db save_db(db(db)

)

        if until_db        if until_db:
            async def:
            async def cleanup cleanup():
                db2 =():
                db2 = load_db()
                load_db if "()
                if "mutes" inmutes" in db2:
                    db2 db2:
                    db2["m["mutes"]utes"] = [m for m in = [m for m in db2 db2["m["mutesutes"]
                                   "]
                                    if not (m if not["target (m["target_id"] == target_id"] == target_id and_id and m["until_date m["until_date"] =="] == until_db)]
                until_db)]
                save_db save_db(db2(db2)
           )
            delay = delay = until_db - now until_db - now.t.timestamp()
            if delay >imestamp()
            if delay > 0 0:
               :
                asyncio.create asyncio.create_task(delayed_task(delayed_task(delay,_task(delay, cleanup cleanup))

        try:
           ))

        try:
            target_user = await target_user = await context.bot context.bot.get.get_chat(target_id_chat(target_id)
            target_name)
            target_name = f"@{ = f"@{target_user.username}"target_user.username}" if target if target_user.username_user.username else target else target_user.first_name_user.first
        except:
           _name
        except:
            target_name = f"ID target_name = f"ID {target {target_id_id}"
        await}"
        await add_to add_to_history(context, buyer_history(context, buyer.id, "М.id, "Мут", f"{targetут", f"{target_name_name} на {D} на {DURATION_LABURATION_LABELS[dELS[dur]}")
        awaitur]}")
        notify await notify_admins(context,_admins(context, f" f"🔇🔇 { {buybuyer_name} ({er_name} ({buybuyer_mentioner}) за_mention}) замутил {мутил {target_name} наtarget_name} на {D {DURATION_LABURATION_LABELS[dur]}ELS[dur]}.")
       .")
        await context.bot await context.bot.send_message(
           .send_message(
            GROUP_CHAT_ID GROUP_CH,
           AT_ID,
            f"🔇 f"🔇 {buy {buyer_mention}er_mention} заму замутитил {target_name}л {target_name} на {DUR на {ATION_LDURATION_LABELS[durABELS[dur]}.",
            reply]}.",
            reply_mark_markup=InlineKeyboardup=InlineKeyboardMarkupMarkup([([[
[
                InlineKeyboard                InlineKeyboardButton("Button("Сделать такСдела же",ть так же", url=f"https://t.me/{ url=f"https://t.me/{context.bcontext.bot.usernameot.username}?}?start=startstart=start")
            ]]),
        )

    elif payload.startswith("un")
            ]]),
        )

    elif payload.startswith("unmutemute_"_"):
        target):
        target_id =_id = int(p int(payload.splitayload.split("_("_")")[1[1])
        try])
        try:
           :
            await context.bot await context.bot.restrict.restrict_chat_chat_member(
               _member(
                chat_id=GROUP chat_id=_CHATGROUP_CHAT_ID_ID,
               ,
                user_id= user_idtarget_id=target_id,
                permissions=get,
                permissions=get_unm_unmute_perute_permissionsmissions(),
                until(),
                until_date=_date=00,
            )
        except,
            )
        except Exception as Exception as e e:
            await:
            await update.message update.message.reply_text(f.reply_text(f""❌ Не❌ Не удалось разму удалось размутить: {eтить: {e}")
            return}")
            return

        db

        db = load_db = load_db()
        if "m()
        if "mutes"utes" in db in db:
           :
            db[" db["mutesmutes"] ="] = [m [m for m in db for m in db["mutes"]["m if mutes"] if m["target_id"]["target_id"] != target_id != target_id]
        save]
        save_db(db)

       _db(db)

        try:
            target try:
            target_user = await context_user = await context.bot.get_chat(target_id)
            target.bot.get_chat(target_id)
            target_name =_name = f"@{target f"@{target_user.username_user.username}" if}" if target_user.username else target_user target_user.username else.first_name target_user.first_name
        except
        except:
            target_name = f:
            target_name = f""ID {ID {target_idtarget_id}"
       }"
        await add await add_to_history_to_history(context,(context, buyer.id, " buyer.id, "Размут",Размут", target_name target_name)
       )
        await notify_admins await notify_admins(context, f"🔊(context, f"🔊 {buyer_name {buyer_name} ({} ({buyerbuyer_mention}) размутил {_mention}) размутил {target_name}target_name}.")
        await.")
        await context.b context.bot.sendot.send_message_message(
            GROUP(
            GROUP_CHAT_ID,
           _CHAT_ID,
            f" f"🔊 {🔊 {buyer_mentionbuyer_mention} раз} размутил {мутил {target_nametarget_name}}.",
           .",
            reply_markup= reply_markup=InlineKeyboardInlineKeyboardMarkupMarkup([([[
                In[
                InlineKeyboardlineKeyboardButton("Button("СделаСделать такть так же", же", url=f url=f"https"https://://t.me/{t.me/{context.bot.username}?start=start")
            ]]),
context.bot.username}?start=start")
            ]]        )

# -----------------------------------------------------------------),
        )

#--
# В -----------------------------------------------------------------ос--
# Встановосстановление задачление задач при запу при запускеске
#
# -------------------------------------------------------------------
async -------------------------------------------------------------------
async def restore def restore_scheduled_scheduled_jobs(app:_jobs Application(app: Application):
    db = load):
    db = load_db_db()
    now()
    now = datetime = datetime.ut.utcnowcnow().timestamp().timestamp()

   ()

    for prefix for prefix in db in db.get("prefixes.get("", []prefixes", []):
       ):
        if prefix.get(" if prefix.get("expires_atexpires_at"):
            delay = prefix"):
            delay["exp = prefix["expires_atires_at"] -"] - now now
            if
            if delay > delay > 0 0:
               :
                async def async def dem demote_restote_restored(uid=ored(uid=prefix["user_idprefix["user_id"], title"], title=prefix["title=prefix["title"]):
                    try:
                        await app"]):
                    try:
                        await app.bot.prom.botote_ch.promote_chat_mat_member(
                            chatember(
                            chat_id=GROUP_CH_id=GROUP_CHAT_IDAT_ID,
                            user_id,
                            user_id=uid=uid,
                            is_,
                            is_anonymous=False,
                           anonymous=False,
                            can_ can_manage_chat=False,
manage_chat=False,
                                                       can can_change_info=False_change_info=False,
                           ,
                            can_delete_messages can_delete_messages=False,
                            can=False,
                            can_inv_invite_users=Falseite_users=False,
                            can_rest,
                            can_restrict_mrict_members=False,
                           embers=False,
                            can_pin_m can_pin_messages=Falseessages=False,
,
                            can_p                            can_promoteromote_members_members=False,
                            can_manage=False,
                            can_manage_video_chats_video=False_chats=False,
                            can_manage,
                            can_manage_topics_topics=False,
                       =False,
                        )
                        )
                        db db2 =2 = load_db load_db()
                       ()
                        if " if "prefixprefixes" ines db2" in db2:
                            db2:
                            db2["prefix["prefixes"]es"] = = [p for [p for p in p in db2["prefix db2["prefixes"] if notes"] (p if not (p["user["user_id"] == uid_id"] == uid and p and p["title"]["title"] == == title title)]
                        save_db(db)]
                        save_db(db2)
                    except2)
                    except Exception as e Exception as:
                        logging e:
                        logging.error(f"О.error(f"Ошибкашибка восстановленного восстановленного дем демооутаута: {: {ee}")
                as}")
                asyncio.create_taskyncio.create(del_task(delayedayed_task(delay_task(delay, demote_, demote_restoredrestored))

   ))

    for mute for mute in db in db.get(".get("mutesmutes", []", []):
):
        if mute       .get(" if mute.get("until_dateuntil_date"):
            delay ="):
            mute[" delay = mute["until_date"] -until_date"] - now
            if now delay >
            if delay > 0:
                0:
                async def async def cleanup_ cleanup_restoredrestored(tid=mute(tid=mute["target_id"], until=m["target_id"], until=muteute["until_date"]["until_date):
                    db"]2 = load):
                    db2 =_db load_db()
                   ()
                    if " if "mutesmutes" in" in db2 db2:
                       :
                        db2 db2["m["mutes"]utes"] = = [m for [m for m in m in db2 db2["mutes"]
                                        if not (m["mutes"]
                                        if not (m["target["target_id"]_id"] == tid and == tid and m m["until_date"]["until == until_date"])]
                    == until)]
                    save_db(db2 save_db(db2)
                asyn)
                asyncio.create_task(dcio.create_task(delayed_task(delayed_task(delay,elay, cleanup_ cleanup_restoredrestored))

# -----------------------------------------------------------------))

# -------------------------------------------------------------------
# Обработ--
# Очик кнопокбработчик к
#нопок
# -------------------------------------------------------------------
async def -------------------------------------------------------------------
 button_handlerasync def button_handler(update: Update(update:, context: Context Update, context: ContextTypes.DTypes.DEFAULT_TYPE):
   EFAULT_TYPE):
    query = update.callback_query query = update.callback_query
   
    data = query.data data = query.data
   
    if data if data == " == "profileprofile":
        await":
        await show show_profile(update_profile(update, context, context)
    elif data)
    elif data == == " "shop":
        await show_shop(update,shop":
        await show_shop(update, context)
    elif context)
    elif data == data == "main "main_menu_menu":
        await":
        await main_menu main_menu_callback_callback(update, context(update, context)
   )
    elif data elif data == " == "shop_prefixshop_prefix":
":
               await show await show_prefix_card_prefix_card(update, context(update, context)
   )
    elif data elif data.startswith("prefix.startswith("_dur_"prefix_dur):
        await_"):
        await handle_prefix handle_prefix_duration_duration(update, context(update, context)
    elif data == ")
    elif data == "shop_mshop_muteute":
        await show_mute_card(update":
        await show_mute_card(update, context, context)
   )
    elif data.startswith elif data.startswith("m("mute_dur_"ute_dur_"):
       ):
        await handle_mute await handle_mute_duration_duration(update, context(update, context)
    elif data)
    elif data == " == "shop_unmuteshop_unmute":
       ":
        await show_unm await show_unmute_cardute_card(update, context(update, context)

# -----------------------------------------------------------------)

#--
# За -------------------------------------------------------------------
# Запуск
#пуск
# -------------------------------------------------------------------
def main -----------------------------------------------------------------():
   --
def main():
    logging.basicConfig logging.basicConfig(
        format="(
       %(as format="%(asctime)s -ctime)s - %( %(name)s - %name)s - %(level(levelname)s - %name)s - %(message(message)s)s",
        level=logging",
        level=logging..INFOINFO,
    )
    app = Application.b,
    )
    app = Application.builder().uilder().token(BOT_TOKENtoken(B).buildOT_TOKEN).build()

    app.add()

    app.add_handler(_handler(CommandHandlerCommandHandler("start", start))
   ("start", start))
    app.add app.add_handler(_handler(CommandHandler("adminCommandHandler", admin("admin", admin_command_command))
    app.add_handler(Command))
    app.add_handler(CommandHandler("Handler("mutemute", cmd", cmd_mute_mute))
   ))
    app app.add.add_handler(CommandHandler("unmute", cmd_handler(CommandHandler_unm("unmute", cmd_unmuteute))
    app))
    app.add_handler(Callback.add_handlerQueryHandler(CallbackQueryHandler(button(button_handler_handler))
   ))
    app.add_handler app.add_handler(Message(MessageHandler(filters.TEXT &Handler(filters.TEXT & ~ ~filters.COfMMANDilters.CO, handleMMAND, handle_target_input))
   _target_input))
    app.add_handler( app.add_handler(PreCheckoutPreCheckoutQueryHandler(precheckoutQueryHandler(precheckout))
    app.add))
    app.add_handler(_handler(MessageHandler(filters.SMessageHandler(filtersUCCESSF.SUCCESSFUL_PUL_PAYMENTAYMENT, successful, successful_payment_payment))

   ))

    async def async def post_init post_init(application):
       (application):
        await restore await restore_scheduled_scheduled_jobs_jobs(application(application)
   )
    app.post app.post_init = post_init_init =

    post_init

    app.run_poll app.run_pollinging()

if __name__()

if __ == "__name__ == "__main__":
    mainmain()
__":
    main()
