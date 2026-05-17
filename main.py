import asyncio
import json
import os
import time

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery
)

from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.methods import RestrictChatMember
from aiogram.types.chat_permissions import ChatPermissions

from config import *

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

DB_FILE = "database.json"


# =========================
# DATABASE
# =========================

def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({"prefixes": [], "mutes": []}, f)

    with open(DB_FILE, "r") as f:
        return json.load(f)


def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)


# =========================
# MENUS
# =========================

def main_menu():
    kb = InlineKeyboardBuilder()

    kb.button(text="👤 Profile", callback_data="profile")
    kb.button(text="🛒 Shop", callback_data="shop")

    kb.adjust(2)

    return kb.as_markup()


def shop_menu():
    kb = InlineKeyboardBuilder()

    kb.button(text="🟢 Prefix", callback_data="product_prefix")
    kb.button(text="🔇 Mute", callback_data="product_mute")
    kb.button(text="🔊 Unmute", callback_data="product_unmute")

    kb.button(text="⬅ Back", callback_data="back_main")

    kb.adjust(1)

    return kb.as_markup()


# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    text = f"""
<b>✨ Welcome to Group Shop</b>

Buy prefixes, mutes and more using Telegram Stars.

Choose a section below.
"""

    await message.answer(
        text,
        reply_markup=main_menu()
    )


# =========================
# NAVIGATION
# =========================

@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>🏠 Main Menu</b>",
        reply_markup=main_menu()
    )


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    user = callback.from_user

    text = f"""
<b>👤 Your Profile</b>

🆔 ID: <code>{user.id}</code>
👤 Username: @{user.username}
⭐ Telegram Stars supported
"""

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="back_main"
                )
            ]
        ]
    )

    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data == "shop")
async def shop(callback: CallbackQuery):
    text = """
<b>🛒 Shop</b>

Choose product below.
"""

    await callback.message.edit_text(
        text,
        reply_markup=shop_menu()
    )


# =========================
# PREFIX PRODUCT
# =========================

@dp.callback_query(F.data == "product_prefix")
async def prefix_product(callback: CallbackQuery):

    text = """
<b>🟢 GROUP PREFIX</b>

Buy green Telegram group prefix/tag.

Available durations:
• 10 minutes
• 1 hour
• 5 hours
• 10 hours
• 24 hours
• Forever

After payment bot will give you prefix automatically.
"""

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="10m - 50⭐", callback_data="buy_prefix_10m")
            ],
            [
                InlineKeyboardButton(text="1h - 80⭐", callback_data="buy_prefix_1h")
            ],
            [
                InlineKeyboardButton(text="5h - 120⭐", callback_data="buy_prefix_5h")
            ],
            [
                InlineKeyboardButton(text="10h - 180⭐", callback_data="buy_prefix_10h")
            ],
            [
                InlineKeyboardButton(text="24h - 250⭐", callback_data="buy_prefix_24h")
            ],
            [
                InlineKeyboardButton(text="Forever - 400⭐", callback_data="buy_prefix_forever")
            ],
            [
                InlineKeyboardButton(text="⬅ Back", callback_data="shop")
            ]
        ]
    )

    await callback.message.edit_text(text, reply_markup=kb)


# =========================
# MUTE PRODUCT
# =========================

user_mute_data = {}

@dp.callback_query(F.data == "product_mute")
async def mute_product(callback: CallbackQuery):

    text = """
<b>🔇 MUTE PRODUCT</b>

Mute any user in the group.

Choose mute duration.
"""

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="10m - 50⭐", callback_data="mute_10m")],
            [InlineKeyboardButton(text="1h - 80⭐", callback_data="mute_1h")],
            [InlineKeyboardButton(text="5h - 150⭐", callback_data="mute_5h")],
            [InlineKeyboardButton(text="10h - 220⭐", callback_data="mute_10h")],
            [InlineKeyboardButton(text="24h - 300⭐", callback_data="mute_24h")],
            [InlineKeyboardButton(text="Forever - 1000⭐", callback_data="mute_forever")],
            [InlineKeyboardButton(text="⬅ Back", callback_data="shop")]
        ]
    )

    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("mute_"))
async def mute_select(callback: CallbackQuery):

    duration = callback.data.split("_")[1]

    user_mute_data[callback.from_user.id] = duration

    await callback.message.edit_text(
        f"""
<b>🔇 Enter Target User</b>

Send username or Telegram ID.
"""
    )


# =========================
# UNMUTE PRODUCT
# =========================

user_unmute = {}

@dp.callback_query(F.data == "product_unmute")
async def unmute_product(callback: CallbackQuery):

    text = f"""
<b>🔊 UNMUTE PRODUCT</b>

Remove mute from user.

Price: {UNMUTE_PRICE}⭐
"""

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Buy for {UNMUTE_PRICE}⭐",
                    callback_data="buy_unmute"
                )
            ],
            [
                InlineKeyboardButton(text="⬅ Back", callback_data="shop")
            ]
        ]
    )

    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data == "buy_unmute")
async def buy_unmute(callback: CallbackQuery):

    await callback.message.edit_text(
        """
<b>🔊 Enter user username or ID for unmute</b>
"""
    )

    user_unmute[callback.from_user.id] = True


# =========================
# RECEIVE USER INPUT
# =========================

@dp.message()
async def handle_input(message: Message):

    uid = message.from_user.id

    # MUTE TARGET
    if uid in user_mute_data:

        duration = user_mute_data[uid]
        target = message.text

        prices = MUTE_PRICES[duration]

        await bot.send_invoice(
            chat_id=uid,
            title="Mute Purchase",
            description=f"Mute user for {duration}",
            payload=f"mute:{duration}:{target}",
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=[LabeledPrice(label="Mute", amount=prices)],
            start_parameter="mute"
        )

        return

    # UNMUTE TARGET
    if uid in user_unmute:

        target = message.text

        await bot.send_invoice(
            chat_id=uid,
            title="Unmute Purchase",
            description="Unmute user",
            payload=f"unmute:{target}",
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label="Unmute",
                    amount=UNMUTE_PRICE
                )
            ],
            start_parameter="unmute"
        )

        return


# =========================
# PREFIX PAYMENTS
# =========================

@dp.callback_query(F.data.startswith("buy_prefix_"))
async def buy_prefix(callback: CallbackQuery):

    duration = callback.data.replace("buy_prefix_", "")

    price = PREFIX_PRICES[duration]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Group Prefix",
        description=f"Buy prefix for {duration}",
        payload=f"prefix:{duration}",
        provider_token=PROVIDER_TOKEN,
        currency="XTR",
        prices=[
            LabeledPrice(
                label="Prefix",
                amount=price
            )
        ],
        start_parameter="prefix"
    )


# =========================
# PAYMENT
# =========================

@dp.pre_checkout_query()
async def checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(
        pre_checkout_query.id,
        ok=True
    )


# =========================
# SUCCESS PAYMENT
# =========================

@dp.message(F.successful_payment)
async def successful_payment(message: Message):

    payload = message.successful_payment.invoice_payload

    buyer = message.from_user

    # PREFIX
    if payload.startswith("prefix:"):

        duration = payload.split(":")[1]

        await bot.send_message(
            GROUP_ID,
            f"""
🟢 <b>{buyer.full_name}</b> bought a prefix for <b>{duration}</b>
""",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔥 Do the same",
                            url=f"https://t.me/{(await bot.me()).username}"
                        )
                    ]
                ]
            )
        )

        for admin in ADMINS:
            await bot.send_message(
                admin,
                f"""
🟢 NEW PREFIX PURCHASE

Buyer: @{buyer.username}
Duration: {duration}
"""
            )

        await message.answer("✅ Prefix purchased successfully!")

    # MUTE
    elif payload.startswith("mute:"):

        _, duration, target = payload.split(":")

        try:
            target_id = int(target)

            until = get_until_time(duration)

            permissions = ChatPermissions(
                can_send_messages=False
            )

            await bot.restrict_chat_member(
                chat_id=GROUP_ID,
                user_id=target_id,
                permissions=permissions,
                until_date=until
            )

            await bot.send_message(
                GROUP_ID,
                f"""
🔇 {buyer.full_name} muted user for {duration}
""",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔥 Do the same",
                                url=f"https://t.me/{(await bot.me()).username}"
                            )
                        ]
                    ]
                )
            )

            for admin in ADMINS:
                await bot.send_message(
                    admin,
                    f"""
🔇 NEW MUTE

Buyer: @{buyer.username}
Target: {target_id}
Duration: {duration}
"""
                )

            await message.answer("✅ User muted!")

        except Exception as e:
            await message.answer(f"Error: {e}")

    # UNMUTE
    elif payload.startswith("unmute:"):

        target = payload.split(":")[1]

        try:

            target_id = int(target)

            permissions = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )

            await bot.restrict_chat_member(
                chat_id=GROUP_ID,
                user_id=target_id,
                permissions=permissions
            )

            await bot.send_message(
                GROUP_ID,
                f"""
🔊 {buyer.full_name} unmuted user
""",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔥 Do the same",
                                url=f"https://t.me/{(await bot.me()).username}"
                            )
                        ]
                    ]
                )
            )

            for admin in ADMINS:
                await bot.send_message(
                    admin,
                    f"""
🔊 NEW UNMUTE

Buyer: @{buyer.username}
Target: {target_id}
"""
                )

            await message.answer("✅ User unmuted!")

        except Exception as e:
            await message.answer(f"Error: {e}")


# =========================
# TIME
# =========================

def get_until_time(duration):

    now = int(time.time())

    mapping = {
        "10m": now + 600,
        "1h": now + 3600,
        "5h": now + 18000,
        "10h": now + 36000,
        "24h": now + 86400,
        "forever": now + 999999999
    }

    return mapping[duration]


# =========================
# RUN
# =========================

async def main():
    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
