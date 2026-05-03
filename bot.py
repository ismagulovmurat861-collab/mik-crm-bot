"""
Telegram-бот для сбора объявлений от собственников недвижимости в Астане.
Установка: pip install python-telegram-bot==20.7
Запуск: python bot.py
"""

import logging
import json
import os
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

# ─── Настройки ───────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")   # от @BotFather
ADMIN_CHAT_ID =     '6556185395'             # ваш Telegram ID (узнать у @userinfobot)
DB_FILE = "listings.json"                 # файл-база данных

logging.basicConfig(level=logging.INFO)

# ─── Шаги диалога ────────────────────────────────────────────
(
    STEP_TYPE,
    STEP_ROOMS,
    STEP_DISTRICT,
    STEP_ADDRESS,
    STEP_AREA,
    STEP_FLOOR,
    STEP_PRICE,
    STEP_DESCRIPTION,
    STEP_PHOTOS,
    STEP_CONTACT,
    STEP_CONFIRM,
) = range(11)

DISTRICTS = [
    "Есиль", "Алматы", "Байконур", "Сарыарка",
    "Нура", "Целиноградский", "Другой"
]

PROPERTY_TYPES = ["Квартира", "Дом", "Участок", "Коммерция"]
ROOMS = ["Студия", "1", "2", "3", "4+"]


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def kb(options, cols=2):
    """Создать клавиатуру из списка опций."""
    rows = [options[i:i+cols] for i in range(0, len(options), cols)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


# ─── /start ──────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    ctx.user_data["photos"] = []
    await update.message.reply_text(
        "🏠 *Добавить объект недвижимости в Астане*\n\n"
        "Я задам несколько вопросов — это займёт 2 минуты.\n"
        "Ваш объект увидят покупатели без посредников.\n\n"
        "Выберите тип объекта:",
        parse_mode="Markdown",
        reply_markup=kb(PROPERTY_TYPES)
    )
    return STEP_TYPE


async def step_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["type"] = update.message.text
    if update.message.text == "Квартира":
        await update.message.reply_text("Сколько комнат?", reply_markup=kb(ROOMS))
        return STEP_ROOMS
    else:
        ctx.user_data["rooms"] = "—"
        await update.message.reply_text("Выберите район:", reply_markup=kb(DISTRICTS))
        return STEP_DISTRICT


async def step_rooms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["rooms"] = update.message.text
    await update.message.reply_text("Выберите район:", reply_markup=kb(DISTRICTS))
    return STEP_DISTRICT


async def step_district(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["district"] = update.message.text
    await update.message.reply_text(
        "Введите адрес (улица, дом, ЖК):",
        reply_markup=ReplyKeyboardRemove()
    )
    return STEP_ADDRESS


async def step_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["address"] = update.message.text
    await update.message.reply_text("Площадь (м²), например: 54")
    return STEP_AREA


async def step_area(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["area"] = update.message.text
    await update.message.reply_text("Этаж / всего этажей, например: 5/9\n(или напишите «—» если не применимо)")
    return STEP_FLOOR


async def step_floor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["floor"] = update.message.text
    await update.message.reply_text("Цена (₸), например: 35 000 000")
    return STEP_PRICE


async def step_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["price"] = update.message.text
    await update.message.reply_text(
        "Краткое описание объекта:\n"
        "(состояние, ремонт, особенности — или напишите «—»)"
    )
    return STEP_DESCRIPTION


async def step_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["description"] = update.message.text
    await update.message.reply_text(
        "📸 Отправьте фото и видео объекта (до 10 штук).\n"
        "Когда закончите — напишите *готово*.",
        parse_mode="Markdown"
    )
    return STEP_PHOTOS


async def step_photos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.lower() == "готово":
        await update.message.reply_text(
            "📞 Укажите контакт для связи:\n"
            "Нажмите кнопку или введите номер вручную.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
        return STEP_CONTACT

    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
        ctx.user_data["photos"].append({"type": "photo", "file_id": photo_id})
        count = len(ctx.user_data["photos"])
        await update.message.reply_text(f"✅ Фото {count} получено. Ещё или напишите *готово*.", parse_mode="Markdown")

    elif update.message.video:
        video_id = update.message.video.file_id
        ctx.user_data["photos"].append({"type": "video", "file_id": video_id})
        count = len(ctx.user_data["photos"])
        await update.message.reply_text(f"✅ Видео {count} получено. Ещё или напишите *готово*.", parse_mode="Markdown")

    return STEP_PHOTOS


async def step_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone = update.message.contact.phone_number
        name = update.message.contact.first_name or ""
    else:
        phone = update.message.text
        name = update.message.from_user.first_name or ""

    ctx.user_data["contact_phone"] = phone
    ctx.user_data["contact_name"] = name
    ctx.user_data["tg_username"] = update.message.from_user.username or "—"

    d = ctx.user_data
    summary = (
        f"📋 *Проверьте данные:*\n\n"
        f"🏠 Тип: {d.get('type')} | {d.get('rooms', '—')} комн.\n"
        f"📍 Район: {d.get('district')}\n"
        f"🗺 Адрес: {d.get('address')}\n"
        f"📐 Площадь: {d.get('area')} м²\n"
        f"🏢 Этаж: {d.get('floor')}\n"
        f"💰 Цена: {d.get('price')} ₸\n"
        f"📝 Описание: {d.get('description')}\n"
        f"📸 Медиа: {len(d.get('photos', []))} шт.\n"
        f"📞 Контакт: {phone}\n\n"
        f"Всё верно?"
    )
    await update.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=kb(["✅ Подтвердить", "❌ Отменить"])
    )
    return STEP_CONFIRM


async def step_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "Подтвердить" not in update.message.text:
        await update.message.reply_text(
            "Отменено. Напишите /start чтобы начать заново.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    d = ctx.user_data
    listing = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "type": d.get("type"),
        "rooms": d.get("rooms", "—"),
        "district": d.get("district"),
        "address": d.get("address"),
        "area": d.get("area"),
        "floor": d.get("floor"),
        "price": d.get("price"),
        "description": d.get("description"),
        "photos": d.get("photos", []),
        "contact_phone": d.get("contact_phone"),
        "contact_name": d.get("contact_name"),
        "tg_username": d.get("tg_username"),
        "tg_id": update.message.from_user.id,
        "status": "новый",
    }

    db = load_db()
    db.append(listing)
    save_db(db)

    # Уведомление риэлтору
    notify = (
        f"🔔 *Новый объект #{listing['id']}*\n\n"
        f"🏠 {listing['type']} {listing['rooms']} комн.\n"
        f"📍 {listing['district']}, {listing['address']}\n"
        f"📐 {listing['area']} м²  |  🏢 {listing['floor']}\n"
        f"💰 {listing['price']} ₸\n"
        f"📝 {listing['description']}\n"
        f"📸 Медиа: {len(listing['photos'])} шт.\n"
        f"👤 {listing['contact_name']} | {listing['contact_phone']}\n"
        f"✈️ @{listing['tg_username']}"
    )
    try:
        await ctx.bot.send_message(ADMIN_CHAT_ID, notify, parse_mode="Markdown")
        # Отправить медиа риэлтору
        for media in listing["photos"]:
            if media["type"] == "photo":
                await ctx.bot.send_photo(ADMIN_CHAT_ID, media["file_id"])
            elif media["type"] == "video":
                await ctx.bot.send_video(ADMIN_CHAT_ID, media["file_id"])
    except Exception as e:
        logging.error(f"Ошибка отправки риэлтору: {e}")

    await update.message.reply_text(
        "✅ *Объект добавлен!*\n\n"
        "Риэлтор свяжется с вами в ближайшее время.\n"
        "Спасибо, что обратились напрямую — без посредников!",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. /start — начать заново.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─── Запуск ──────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STEP_TYPE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, step_type)],
            STEP_ROOMS:       [MessageHandler(filters.TEXT & ~filters.COMMAND, step_rooms)],
            STEP_DISTRICT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_district)],
            STEP_ADDRESS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, step_address)],
            STEP_AREA:        [MessageHandler(filters.TEXT & ~filters.COMMAND, step_area)],
            STEP_FLOOR:       [MessageHandler(filters.TEXT & ~filters.COMMAND, step_floor)],
            STEP_PRICE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, step_price)],
            STEP_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_description)],
            STEP_PHOTOS:      [MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO, step_photos)],
            STEP_CONTACT:     [MessageHandler(filters.TEXT | filters.CONTACT, step_contact)],
            STEP_CONFIRM:     [MessageHandler(filters.TEXT & ~filters.COMMAND, step_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    print("✅ Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling()


if __name__ == "__main__":
    main()