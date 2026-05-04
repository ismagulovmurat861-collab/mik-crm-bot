import logging
import json
import os
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 6556185395
DB_FILE = "db.json"

logging.basicConfig(level=logging.INFO)

# ===== ЭТАПЫ =====
STEP_MENU, STEP_TYPE, STEP_DISTRICT, STEP_PRICE, STEP_PHOTOS, STEP_CONTACT = range(6)

PROPERTY_TYPES = ["Квартира", "Дом", "Коммерция"]

# ===== БАЗА =====
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== СТАРТ =====
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [["🚀 Продать объект", "📊 Узнать цену"]]
    await update.message.reply_text(
        "🏠 Продам вашу квартиру быстрее и дороже рынка\n\n"
        "— Есть база покупателей\n"
        "— Без пустых показов\n\n"
        "👇 Выберите:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return STEP_MENU

# ===== МЕНЮ =====
async def menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тип объекта?", reply_markup=ReplyKeyboardMarkup([[t] for t in PROPERTY_TYPES], resize_keyboard=True))
    return STEP_TYPE

# ===== ТИП =====
async def step_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["type"] = update.message.text
    await update.message.reply_text("Район?")
    return STEP_DISTRICT

# ===== РАЙОН =====
async def step_district(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["district"] = update.message.text

    await update.message.reply_text(
        "💰 Примерная цена: 30–45 млн ₸\nХотите точную оценку?"
    )

    await update.message.reply_text("Введите вашу цену:")
    return STEP_PRICE

# ===== ЦЕНА =====
async def step_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["price"] = update.message.text
    ctx.user_data["photos"] = []

    await update.message.reply_text("📸 Отправьте фото и напишите 'готово'")
    return STEP_PHOTOS

# ===== ФОТО =====
async def step_photos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text and "готово" in update.message.text.lower():
        await update.message.reply_text("📞 Укажите контакт:")
        return STEP_CONTACT

    if update.message.photo:
        ctx.user_data["photos"].append(update.message.photo[-1].file_id)

    return STEP_PHOTOS

# ===== КОНТАКТ =====
async def step_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["contact"] = update.message.text

    # сохраняем
    db = load_db()
    listing = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "date": datetime.now().strftime("%d.%m.%Y"),
        "data": ctx.user_data,
        "status": "новый"
    }
    db.append(listing)
    save_db(db)

    # уведомление тебе
    await ctx.bot.send_message(ADMIN_CHAT_ID, f"Новый лид:\n{listing}")

    # ответ клиенту
    await update.message.reply_text(
        "🔥 Заявка принята\n"
        "Я уже ищу покупателей\n"
        "⏱ Напишу в течение 10–15 минут"
    )

    # автоворонка
    chat_id = update.message.chat_id

    ctx.job_queue.run_once(follow_up, 3600, chat_id=chat_id, data={"step": 1})
    ctx.job_queue.run_once(follow_up, 86400, chat_id=chat_id, data={"step": 2})

    return ConversationHandler.END

# ===== ВОРОНКА =====
async def follow_up(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = ctx.job.chat_id
    step = ctx.job.data.get("step")

    if step == 1:
        await ctx.bot.send_message(chat_id, "📊 Уже есть интерес к вашему объекту. Хотите ускорить продажу?")

    elif step == 2:
        await ctx.bot.send_message(chat_id, "🔥 Есть потенциальный покупатель. Нужно уточнить детали")

# ===== CRM =====
async def leads(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    text = ""

    for l in db[-10:]:
        text += f"\nID:{l['id']} | {l['data'].get('type')} | {l['data'].get('price')} | {l['status']}"

    await update.message.reply_text(text or "Нет заявок")

async def set_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args

    if len(args) < 2:
        await update.message.reply_text("Формат: /set ID статус")
        return

    db = load_db()

    for l in db:
        if l["id"] == args[0]:
            l["status"] = args[1]
            save_db(db)
            await update.message.reply_text("Обновлено")
            return

    await update.message.reply_text("Не найдено")

# ===== ОТМЕНА =====
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено")
    return ConversationHandler.END

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STEP_MENU: [MessageHandler(filters.TEXT, menu)],
            STEP_TYPE: [MessageHandler(filters.TEXT, step_type)],
            STEP_DISTRICT: [MessageHandler(filters.TEXT, step_district)],
            STEP_PRICE: [MessageHandler(filters.TEXT, step_price)],
            STEP_PHOTOS: [MessageHandler(filters.TEXT | filters.PHOTO, step_photos)],
            STEP_CONTACT: [MessageHandler(filters.TEXT, step_contact)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("leads", leads))
    app.add_handler(CommandHandler("set", set_status))

    print("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()