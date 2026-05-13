import logging
import json
import os
import re
import asyncio
from datetime import datetime

import psycopg2
import psycopg2.extras
import httpx

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from openai import AsyncOpenAI

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# =========================================================
# LOAD ENV
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
DB_URL = os.getenv("DATABASE_URL")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not DB_URL:
    raise ValueError("DATABASE_URL not found")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)

log = logging.getLogger(_name_)

# =========================================================
# OPENAI
# =========================================================

ai = None

if OPENAI_KEY:
    try:
        ai = AsyncOpenAI(api_key=OPENAI_KEY)
    except Exception as e:
        log.error(f"OpenAI init error: {e}")

# =========================================================
# CONFIG
# =========================================================

HEADERS = {
    "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

(
    STEP_TYPE,
    STEP_ROOMS,
    STEP_DISTRICT,
    STEP_ADDRESS,
    STEP_AREA,
    STEP_FLOOR,
    STEP_PRICE,
    STEP_DESC,
    STEP_PHOTOS,
    STEP_CONTACT,
    STEP_CONFIRM
) = range(11)

DISTRICTS = [
    "Есиль",
    "Алматы",
    "Байконур",
    "Сарыарка",
    "Нура",
]

TYPES = [
    "Квартира",
    "Дом",
    "Коммерция",
]

ROOMS = [
    "1",
    "2",
    "3",
    "4+",
]

# =========================================================
# DATABASE
# =========================================================

def get_db():
    return psycopg2.connect(
        DB_URL.replace("postgres://", "postgresql://"),
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def init_db():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                date TEXT,
                type TEXT,
                rooms TEXT,
                district TEXT,
                address TEXT,
                area TEXT,
                floor TEXT,
                price TEXT,
                description TEXT,
                contact_phone TEXT,
                contact_name TEXT,
                tg_username TEXT,
                tg_id BIGINT,
                funnel_stage TEXT DEFAULT 'лид',
                source TEXT DEFAULT 'manual'
            )
            """)
        conn.commit()

def sql(query, params=None, fetch=None):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(query, params or ())
            conn.commit()

            if fetch == "all":
                return [dict(r) for r in c.fetchall()]

            if fetch == "one":
                r = c.fetchone()
                return dict(r) if r else None

def add_listing(data):
    sql("""
    INSERT INTO listings(
        id,
        date,
        type,
        rooms,
        district,
        address,
        area,
        floor,
        price,
        description,
        contact_phone,
        contact_name,
        tg_username,
        tg_id,
        funnel_stage,
        source
    )
    VALUES(
        %(id)s,
        %(date)s,
        %(type)s,
        %(rooms)s,
        %(district)s,
        %(address)s,
        %(area)s,
        %(floor)s,
        %(price)s,
        %(description)s,
        %(contact_phone)s,
        %(contact_name)s,
        %(tg_username)s,
        %(tg_id)s,
        %(funnel_stage)s,
        %(source)s
    )
    ON CONFLICT (id) DO NOTHING
    """, data)

def load_db():
    return sql(
        "SELECT * FROM listings ORDER BY date DESC",
        fetch="all"
    ) or []

# =========================================================
# AI
# =========================================================

AGENT_SYSTEM = """
Ты — AI агент MiK Real Estate.

Твоя задача:
- понять потребность клиента
- помочь подобрать недвижимость
- предложить просмотр
- получить контакт

Стиль:
- профессиональный
- краткий
- дружелюбный

Не придумывай объекты.
"""

async def ai_reply(text: str):

    if not ai:
        return "Здравствуйте! Чем могу помочь по недвижимости?"

    try:
        response = await ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": AGENT_SYSTEM
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            max_tokens=300
        )

        return response.choices[0].message.content

    except Exception as e:
        log.error(e)
        return "Ошибка AI."

# =========================================================
# PARSERS
# =========================================================

async def parse_krisha():

    results = []

    async with httpx.AsyncClient(
        headers=HEADERS,
        timeout=30
    ) as c:

        try:

            r = await c.get(
                "https://krisha.kz/prodazha/kvartiry/astana/"
            )

            soup = BeautifulSoup(r.text, "html.parser")

            cards = soup.select(
                "div.a-card, div.a-card__inc"
            )

            for card in cards[:10]:

                title = card.get_text(" ", strip=True)

                results.append({
                    "title": title
                })

        except Exception as e:
            log.error(e)

    return results

# =========================================================
# HELPERS
# =========================================================

def is_admin(update):

    try:
        return update.effective_user.id == ADMIN_ID
    except:
        return False

def rkb(items, cols=2):

    rows = [
        items[i:i+cols]
        for i in range(0, len(items), cols)
    ]

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=True
    )

# =========================================================
# START
# =========================================================

async def cmd_start(update: Update, ctx):

    if is_admin(update):

        await update.message.reply_text(
            "🏢 MiK Real Estate CRM"
        )

        return

    await update.message.reply_text(
        "Добро пожаловать!\n\n"
        "Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(
            [
                ["🏠 Подать объявление"],
                ["💬 Подобрать недвижимость"],
            ],
            resize_keyboard=True
        )
    )

# =========================================================
# CLIENT CHAT
# =========================================================

async def handle_client(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return ConversationHandler.END

    if is_admin(update):
        return ConversationHandler.END

    text = update.message.text or ""

    # START FORM

    if text == "🏠 Подать объявление":

        ctx.user_data.clear()

        await update.message.reply_text(
            "Выберите тип:",
            reply_markup=rkb(TYPES)
        )

        return STEP_TYPE

    # AI CHAT

    await update.message.chat.send_action("typing")

    reply = await ai_reply(text)

    await update.message.reply_text(reply)

    await asyncio.sleep(0.7)

    return ConversationHandler.END

# =========================================================
# FORM
# =========================================================

async def step_type(update, ctx):

    ctx.user_data["type"] = update.message.text

    await update.message.reply_text(
        "Сколько комнат?",
        reply_markup=rkb(ROOMS)
    )

    return STEP_ROOMS

async def step_rooms(update, ctx):

    ctx.user_data["rooms"] = update.message.text

    await update.message.reply_text(
        "Район:",
        reply_markup=rkb(DISTRICTS)
    )

    return STEP_DISTRICT

async def step_district(update, ctx):

    ctx.user_data["district"] = update.message.text

    await update.message.reply_text(
        "Адрес:",
        reply_markup=ReplyKeyboardRemove()
    )

    return STEP_ADDRESS

async def step_address(update, ctx):

    ctx.user_data["address"] = update.message.text

    await update.message.reply_text(
        "Площадь:"
    )

    return STEP_AREA

async def step_area(update, ctx):

    ctx.user_data["area"] = update.message.text

    await update.message.reply_text(
        "Этаж:"
    )

    return STEP_FLOOR

async def step_floor(update, ctx):

    ctx.user_data["floor"] = update.message.text

    await update.message.reply_text(
        "Цена:"
    )

    return STEP_PRICE

async def step_price(update, ctx):

    ctx.user_data["price"] = update.message.text

    await update.message.reply_text(
        "Описание:"
    )

    return STEP_DESC

async def step_desc(update, ctx):

    ctx.user_data["description"] = update.message.text

    await update.message.reply_text(
        "Телефон:"
    )

    return STEP_CONTACT

async def step_contact(update, ctx):

    phone = update.message.text

    data = ctx.user_data

    lid = datetime.now().strftime("%Y%m%d%H%M%S")

    listing = {
        "id": lid,
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "type": data.get("type"),
        "rooms": data.get("rooms"),
        "district": data.get("district"),
        "address": data.get("address"),
        "area": data.get("area"),
        "floor": data.get("floor"),
        "price": data.get("price"),
        "description": data.get("description"),
        "contact_phone": phone,
        "contact_name": update.effective_user.first_name,
        "tg_username": update.effective_user.username or "—",
        "tg_id": update.effective_user.id,
        "funnel_stage": "лид",
        "source": "manual"
    }

    add_listing(listing)

    await update.message.reply_text(
        "✅ Объявление добавлено!"
    )

    try:

        await ctx.bot.send_message(
            ADMIN_ID,
            f"""
🔔 Новый лид

🏠 {listing['type']}
🛏️ {listing['rooms']}
📍 {listing['address']}
💰 {listing['price']}
📞 {listing['contact_phone']}
"""
        )

        await asyncio.sleep(0.7)

    except Exception as e:
        log.error(e)

    return ConversationHandler.END

# =========================================================
# PARSE COMMAND
# =========================================================

async def cmd_parse(update, ctx):

    if not is_admin(update):
        return

    await update.message.reply_text(
        "🔍 Парсинг..."
    )

    items = await parse_krisha()

    await update.message.reply_text(
        f"Найдено: {len(items)}"
    )

# =========================================================
# CANCEL
# =========================================================

async def cancel(update, ctx):

    await update.message.reply_text(
        "Отменено.",
        reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END

# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler(
        timezone="Asia/Almaty"
    )

    scheduler.start()

    scheduler.print_jobs()

    # =====================================================
    # FORM
    # =====================================================

    conv = ConversationHandler(

        entry_points=[
            MessageHandler(
                filters.Regex("^🏠 Подать объявление$"),
                handle_client
            ),
        ],

        states={

            STEP_TYPE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_type
                )
            ],

            STEP_ROOMS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_rooms
                )
            ],

            STEP_DISTRICT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_district
                )
            ],

            STEP_ADDRESS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_address
                )
            ],

            STEP_AREA: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_area
                )
            ],

            STEP_FLOOR: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_floor
                )
            ],

            STEP_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_price
                )
            ],

            STEP_DESC: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_desc
                )
            ],

            STEP_CONTACT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    step_contact
                )
            ],

        },

        fallbacks=[
            CommandHandler("cancel", cancel)
        ]

    )

    app.add_handler(
        CommandHandler("start", cmd_start)
    )

    app.add_handler(
        CommandHandler("parse", cmd_parse)
    )

    app.add_handler(conv)

    # =====================================================
    # AI CHAT
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & ~filters.User(ADMIN_ID),
            handle_client
        )
    )

    print("✅ MiK Real Estate Bot запущен!")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

# =========================================================

if _name_ == "_main_":
    main()
