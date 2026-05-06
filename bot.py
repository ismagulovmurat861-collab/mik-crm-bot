import logging
import json
import os
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler

BOT_TOKEN = os.getenv("BOT_TOKEN", "8258133350:AAF9QmtTm8qkAZvIOtyE5XYmmES7bq6ZxTg")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "6556185395"))
DB_FILE = "listings.json"

logging.basicConfig(format="%(asctime)s - %(message)s", level=logging.INFO)

STEP_TYPE, STEP_ROOMS, STEP_DISTRICT, STEP_ADDRESS, STEP_AREA, STEP_FLOOR, STEP_PRICE, STEP_DESCRIPTION, STEP_PHOTOS, STEP_CONTACT, STEP_CONFIRM = range(11)

DISTRICTS = ["Есиль", "Алматы", "Байконур", "Сарыарка", "Нура", "Целиноградский", "Другой"]
PROPERTY_TYPES = ["Квартира", "Дом", "Участок", "Коммерция"]
ROOMS = ["Студия", "1", "2", "3", "4+"]
CRM_STATUSES = ["🆕 Новый", "🔄 В работе", "👁️ Показ", "🤝 Переговоры", "✅ Сделка", "❌ Отказ"]

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def kb(options, cols=2):
    rows = [options[i:i+cols] for i in range(0, len(options), cols)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def is_admin(update: Update):
    return update.effective_user.id == ADMIN_CHAT_ID

# ─── ADMIN COMMANDS ───────────────────────────────────────────

async def cmd_crm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Все заявки", callback_data="list_all")],
        [InlineKeyboardButton("🆕 Новые", callback_data="list_новый"),
         InlineKeyboardButton("🔄 В работе", callback_data="list_в работе")],
        [InlineKeyboardButton("👁️ Показ", callback_data="list_показ"),
         InlineKeyboardButton("🤝 Переговоры", callback_data="list_переговоры")],
        [InlineKeyboardButton("✅ Сделки", callback_data="list_сделка"),
         InlineKeyboardButton("❌ Отказы", callback_data="list_отказ")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
    ])
    await update.message.reply_text("🏠 MiK CRM — Панель управления\n\nВыберите раздел:", parse_mode="Markdown", reply_markup=keyboard)

async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    db = load_db()
    new_listings = [l for l in db if l.get("crm_status") == "новый"]
    if not new_listings:
        await update.message.reply_text("✅ Новых заявок нет!")
        return
    await send_listings(update, new_listings, "🆕 Новые заявки")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    db = load_db()
    total = len(db)
    by_status = {}
    for l in db:
        s = l.get("crm_status", "новый")
        by_status[s] = by_status.get(s, 0) + 1
    by_district = {}
    for l in db:
        d = l.get("district", "—")
        by_district[d] = by_district.get(d, 0) + 1
    text = f"📊 Статистика MiK CRM\n\n"
    text += f"📋 Всего заявок: {total}\n\n"
    text += "По статусам:\n"
    for s, c in by_status.items():
        text += f"  • {s}: {c}\n"
    text += "\n*По районам:*\n"
    for d, c in sorted(by_district.items(), key=lambda x: -x[1]):
        text += f"  • {d}: {c}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def send_listings(update, listings, title):
    if not listings:
        await update.message.reply_text(f"{title}\n\nЗаявок нет.", parse_mode="Markdown")
        return
    text = f"{title} ({len(listings)} шт.)\n\n"
    for i, l in enumerate(listings[-10:], 1):
        text += f"{i}. {l.get('type','')} {l.get('rooms','')} комн. — {l.get('district','')}\n"
        text += f"📍 {l.get('address','')}\n"
        text += f"💰 {l.get('price','')} ₸ | 📐 {l.get('area','')} м²\n"
        text += f"👤 {l.get('contact_name','')} | {l.get('contact_phone','')}\n"
        text += f"📅 {l.get('date','')} | #{l.get('id','')[-6:]}\n\n"
    if len(listings) > 10:
        text += f"...и ещё {len(listings)-10} заявок"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад в меню", callback_data="menu")]])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = load_db()
    data = query.data

    if data == "menu":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Все заявки", callback_data="list_all")],
            [InlineKeyboardButton("🆕 Новые", callback_data="list_новый"),
             InlineKeyboardButton("🔄 В работе", callback_data="list_в работе")],
            [InlineKeyboardButton("👁️ Показ", callback_data="list_показ"),
             InlineKeyboardButton("🤝 Переговоры", callback_data="list_переговоры")],
            [InlineKeyboardButton("✅ Сделки", callback_data="list_сделка"),
             InlineKeyboardButton("❌ Отказы", callback_data="list_отказ")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        ])
        await query.edit_message_text("🏠 MiK CRM — Панель управления\n\nВыберите раздел:", parse_mode="Markdown", reply_markup=keyboard)

    elif data == "stats":
        total = len(db)
        by_status = {}
        for l in db:
            s = l.get("crm_status", "новый")
            by_status[s] = by_status.get(s, 0) + 1
        by_district = {}
        for l in db:
            d = l.get("district", "—")
            by_district[d] = by_district.get(d, 0) + 1
        text = f"📊 Статистика MiK CRM\n\n"
        text += f"📋 Всего заявок: {total}\n\n"
        text += "По статусам:\n"
        for s, c in by_status.items():
            text += f"  • {s}: {c}\n"
        text += "\n*По районам:*\n"
        for d, c in sorted(by_district.items(), key=lambda x: -x[1]):
            text += f"  • {d}: {c}\n"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    elif data.startswith("list_"):
        status = data[5:]
        if status == "all":
            listings = db
            title = "📋 Все заявки"
        else:
            listings = [l for l in db if l.get("crm_status") == status]
            title = f"Заявки: {status}"
        if not listings:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu")]])
            await query.edit_message_text(f"{title}\n\nЗаявок нет.", parse_mode="Markdown", reply_markup=keyboard)
            return
        text = f"{title} ({len(listings)} шт.)\n\n"
        for i, l in enumerate(listings[-10:], 1):
            text += f"{i}. {l.get('type','')} {l.get('rooms','')} — {l.get('district','')}\n"
            text += f"📍 {l.get('address','')}\n"
            text += f"💰 {l.get('price','')} ₸ | 📐 {l.get('area','')} м²\n"
            text += f"👤 {l.get('contact_name','')} | {l.get('contact_phone','')}\n"
            text += f"📅 {l.get('date','')} | #{l.get('id','')[-6:]}\n\n"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    elif data.startswith("status_"):
        parts = data.split("_", 2)
        listing_id = parts[1]
        new_status = parts[2]
        for l in db:
            if l["id"] == listing_id:
                l["crm_status"] = new_status
                break
        save_db(db)
        await query.answer(f"✅ Статус изменён на: {new_status}", show_alert=True)

# ─── BOT CONVERSATION ────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Открыть CRM", callback_data="menu")],
        ])
        await update.message.reply_text(
            "👋 Добро пожаловать в MiK CRM!\n\n"
            "Нажмите кнопку ниже для управления заявками\n"
            "или используйте команды:\n"
            "/crm — открыть панель\n"
            "/new — новые заявки\n"
            "/stats — статистика",
            reply_markup=keyboard
        )
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["photos"] = []
    await update.message.reply_text(
        "🏠 Добавить объект в Астане — без посредников!\n\n"
        "Отвечу на несколько вопросов — займёт 2 минуты.\n\n"
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
    ctx.user_data["rooms"] = "—"
    await update.message.reply_text("Выберите район:", reply_markup=kb(DISTRICTS))
    return STEP_DISTRICT

async def step_rooms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["rooms"] = update.message.text
    await update.message.reply_text("Выберите район:", reply_markup=kb(DISTRICTS))
    return STEP_DISTRICT

async def step_district(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["district"] = update.message.text
    await update.message.reply_text("Введите адрес (улица, дом, ЖК):", reply_markup=ReplyKeyboardRemove())
    return STEP_ADDRESS

async def step_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["address"] = update.message.text
    await update.message.reply_text("Площадь (м²), например: 65")
    return STEP_AREA

async def step_area(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["area"] = update.message.text
    await update.message.reply_text("Этаж / всего этажей, например: 5/9")
    return STEP_FLOOR

async def step_floor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["floor"] = update.message.text
    await update.message.reply_text("Цена (₸), например: 35 000 000")
    return STEP_PRICE

async def step_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["price"] = update.message.text
    await update.message.reply_text("Краткое описание (или напишите —):")
    return STEP_DESCRIPTION

async def step_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["description"] = update.message.text
    await update.message.reply_text("📸 Отправьте фото/видео объекта.\nКогда закончите — напишите готово.", parse_mode="Markdown")
    return STEP_PHOTOS

async def step_photos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.lower() == "готово":
        await update.message.reply_text(
            "📞 Укажите контакт для связи:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 Поделиться номером", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
        )
        return STEP_CONTACT
    if update.message.photo:
        ctx.user_data["photos"].append({"type": "photo", "file_id": update.message.photo[-1].file_id})
        await update.message.reply_text(f"✅ Фото {len(ctx.user_data['photos'])} получено. Ещё или напишите готово.", parse_mode="Markdown")
    elif update.message.video:
        ctx.user_data["photos"].append({"type": "video", "file_id": update.message.video.file_id})
        await update.message.reply_text(f"✅ Видео получено. Ещё или напишите готово.", parse_mode="Markdown")
    return STEP_PHOTOS

async def step_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    phone = update.message.contact.phone_number if update.message.contact else update.message.text
    ctx.user_data["contact_phone"] = phone
    ctx.user_data["contact_name"] = update.message.from_user.first_name or ""
    ctx.user_data["tg_username"] = update.message.from_user.username or "—"
    d = ctx.user_data
    summary = (
        f"📋 Проверьте данные:\n\n"
        f"🏠 {d.get('type')} {d.get('rooms','—')} комн.\n"
        f"📍 {d.get('district')}, {d.get('address')}\n"
        f"📐 {d.get('area')} м²  |  🏢 {d.get('floor')}\n"
        f"💰 {d.get('price')} ₸\n"
        f"📝 {d.get('description')}\n"
        f"📸 Медиа: {len(d.get('photos',[]))} шт.\n"
        f"📞 {phone}\n\nВсё верно?"
    )
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=kb(["✅ Подтвердить", "❌ Отменить"]))
    return STEP_CONFIRM

async def step_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "Подтвердить" not in update.message.text:
        await update.message.reply_text("Отменено. /start — начать заново.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    d = ctx.user_data
    listing = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "type": d.get("type"), "rooms": d.get("rooms","—"),
        "district": d.get("district"), "address": d.get("address"),
        "area": d.get("area"), "floor": d.get("floor"),
        "price": d.get("price"), "description": d.get("description"),
        "photos": d.get("photos",[]),
        "contact_phone": d.get("contact_phone"),
        "contact_name": d.get("contact_name"),
        "tg_username": d.get("tg_username"),
        "tg_id": update.message.from_user.id,
        "crm_status": "новый", "notes": [],
    }
    db = load_db()
    db.append(listing)
    save_db(db)

    # Уведомление риэлтору с кнопками смены статуса
    notify = (
        f"🔔 Новая заявка #{listing['id'][-6:]}\n\n"
        f"🏠 {listing['type']} {listing['rooms']} комн.\n"
        f"📍 {listing['district']}, {listing['address']}\n"
        f"📐 {listing['area']} м²  |  🏢 {listing['floor']}\n"
        f"💰 {listing['price']} ₸\n"
        f"📝 {listing['description']}\n"
        f"📸 Медиа: {len(listing['photos'])} шт.\n"
        f"👤 {listing['contact_name']} | {listing['contact_phone']}\n"
        f"✈️ @{listing['tg_username']}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 В работе", callback_data=f"status_{listing['id']}_в работе"),
         InlineKeyboardButton("👁️ Показ", callback_data=f"status_{listing['id']}_показ")],
        [InlineKeyboardButton("🤝 Переговоры", callback_data=f"status_{listing['id']}_переговоры"),
         InlineKeyboardButton("✅ Сделка", callback_data=f"status_{listing['id']}_сделка")],
        [InlineKeyboardButton("❌ Отказ", callback_data=f"status_{listing['id']}_отказ")],
    ])
    try:
        await ctx.bot.send_message(ADMIN_CHAT_ID, notify, parse_mode="Markdown", reply_markup=keyboard)
        for media in listing["photos"]:
            if media["type"] == "photo":
                await ctx.bot.send_photo(ADMIN_CHAT_ID, media["file_id"])
            else:
                await ctx.bot.send_video(ADMIN_CHAT_ID, media["file_id"])
    except Exception as e:
        logging.error(e)

    await update.message.reply_text("✅ Объект добавлен!\n\nРиэлтор свяжется с вами в ближайшее время.", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. /start — начать заново.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STEP_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_type)],
            STEP_ROOMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_rooms)],
            STEP_DISTRICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_district)],
            STEP_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_address)],
            STEP_AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_area)],
            STEP_FLOOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_floor)],
            STEP_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_price)],
            STEP_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_description)],
            STEP_PHOTOS: [MessageHandler(filters.ALL, step_photos)],
            STEP_CONTACT: [MessageHandler(filters.ALL, step_contact)],
            STEP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("crm", cmd_crm))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if _name_ == "_main_":
    main()
