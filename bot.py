import logging, json, os, re, httpx, asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from fastapi import FastAPI
import uvicorn

# Библиотеки для Google Таблиц
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Библиотеки для авто-монтажа видео
from PIL import Image as PILImage, ImageDraw, ImageFont
from moviepy.editor import ImageSequenceClip

# ══════════════════════════════════════════════════
# НАСТРОЙКИ (Render заполнит их сам из Environment)
# ══════════════════════════════════════════════════
BOT_TOKEN       = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН")
ADMIN_ID        = int(os.getenv("ADMIN_CHAT_ID", "0"))
GEMINI_KEY      = os.getenv("GEMINI_API_KEY", "")
SPREADSHEET_ID  = os.getenv("SPREADSHEET_ID", "") 

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Инструкция для ИИ-переговорщика с клиентами в чате
AGENT_SYSTEM = "Ты — ИИ-брокер компании MiK Real Estate в Астане. Твоя цель — вежливо узнать номер телефона клиента для показа объекта. Отвечай кратко, используй эмодзи."

# Промпт для ИИ-продюсера контента
CONTENT_FACTORY_SYSTEM = """Ты — креативный продюсер Reels/TikTok. Твоя задача — вытащить из параметров квартиры в Астане ОДНУ самую цепляющую фразу (ХУК) для обложки видео (максимум 5 слов) и ОДНУ строку с главным преимуществом (максимум 6 слов).
Ответь СТРОГО в формате JSON без лишнего текста и без кавычек markdown:
{"hook": "ЖЕСТКИЙ_ХУК", "sub": "ГЛАВНОЕ_ПРЕИМУЩЕСТВО"}"""

# Создаем веб-сервер, чтобы Render не усыплял бота
api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK Production Factory is running online"}

# ══════════════════════════════════════════════════
# СБОРКА ВИДЕО И НАЛОЖЕНИЕ ТЕКСТА
# ══════════════════════════════════════════════════
def create_auto_video(image_paths, hook_text, sub_text, output_path="result.mp4"):
    try:
        processed_images = []
        
        for i, img_path in enumerate(image_paths):
            with PILImage.open(img_path) as img:
                # Подгоняем фото под вертикальный стандарт Shorts/Reels (1080x1920)
                img.thumbnail((1080, 1920))
                background = PILImage.new('RGB', (1080, 1920), (0, 0, 0))
                offset = ((1080 - img.width) // 2, (1920 - img.height) // 2)
                background.paste(img, offset)
                
                # Текст накладываем только на первые два кадра для удержания внимания
                if i < 2:
                    draw = ImageDraw.Draw(background)
                    try:
                        font_hook = ImageFont.load_default(size=60)
                        font_sub = ImageFont.load_default(size=40)
                    except:
                        font_hook = ImageFont.load_default()
                        font_sub = ImageFont.load_default()
                    
                    # Полупрозрачная темная подложка под титры
                    draw.rectangle([40, 1450, 1040, 1750], fill=(0, 0, 0, 180))
                    
                    # Наносим ХУК (желтый) и Описание (белое)
                    draw.text((1080 // 2, 1510), hook_text, fill=(255, 235, 59), anchor="mm", font=font_hook)
                    draw.text((1080 // 2, 1640), sub_text, fill=(255, 255, 255), anchor="mm", font=font_sub)
                
                frame_path = f"frame_{i}.jpg"
                background.save(frame_path)
                processed_images.append(frame_path)
        
        # Склеиваем видео (по 2 секунды на каждую фотографию)
        clip = ImageSequenceClip(processed_images, fps=0.5)
        clip.write_videofile(output_path, fps=24, codec="libx264", logger=None)
        
        # Удаляем временные картинки-кадры из памяти сервера
        for f in processed_images:
            if os.path.exists(f): os.remove(f)
            
        return output_path
    except Exception as e:
        log.error(f"💥 Ошибка сборки видеоролика: {e}")
        return None

# ══════════════════════════════════════════════════
# ИНТЕГРАЦИЯ С GOOGLE ТАБЛИЦАМИ
# ══════════════════════════════════════════════════
def get_sheets_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = os.getenv("GOOGLE_CREDS_JSON")
    if creds_json:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    return gspread.authorize(creds)

def save_object_to_sheet(date, source, title, price, phone, user, notes=""):
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SPREADSHEET_ID).get_worksheet(0) # Первое окно (База)
        sheet.append_row([date, source, title, price, phone, user, notes])
    except Exception as e: log.error(f"Таблицы (Объект): {e}")

def save_content_to_sheet(date, object_info, platform, scenario):
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SPREADSHEET_ID).get_worksheet(1) # Второе окно (Контент-завод)
        sheet.append_row([date, object_info, platform, scenario])
    except Exception as e: log.error(f"Таблицы (Контент): {e}")

# ══════════════════════════════════════════════════
# ГЕНЕРАЦИЯ ДАННЫХ ЧЕРЕЗ GEMINI
# ══════════════════════════════════════════════════
async def get_ai_titles(title, price):
    if not GEMINI_KEY: return {"hook": "Срочный объект!", "sub": "Цена ниже рынка в Астане"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"Квартира: {title}, Цена: {price}. Сделай JSON-титры."
    
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json={
                "systemInstruction": {"parts": [{"text": CONTENT_FACTORY_SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }, timeout=12.0)
            return json.loads(r.json()['candidates'][0]['content']['parts'][0]['text'])
    except Exception as e:
        log.error(f"Ошибка Gemini ИИ: {e}")
        return {"hook": "Горячая продажа!", "sub": f"{title} в Астане"}

# ══════════════════════════════════════════════════
# ПАРСЕР И АВТОМАТИЧЕСКИЙ КОНТЕНТ-ЗАВОД
# ══════════════════════════════════════════════════
PARSED_CACHE = set()

async def auto_production_job(bot):
    log.info("🔍 Парсер запущен. Проверяем площадки...")
    
    # Сюда зашивается логика BeautifulSoup для Крыши / OLX
    demo_id = f"parsed_{datetime.now().strftime('%H%M')}"
    if demo_id in PARSED_CACHE: return
    PARSED_CACHE.add(demo_id)
    
    title = "2-комнатная квартира, ЖК Времена года"
    price = "29 000 000 ₸"
    phone = "+7702888XX"
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Имитируем скачивание 3-х фотографий из объявления
    local_images = []
    for color, name in [((180,40,40), "t1.jpg"), ((40,180,40), "t2.jpg"), ((40,40,180), "t3.jpg")]:
        img = PILImage.new('RGB', (800, 600), color)
        img.save(name)
        local_images.append(name)
        
    # Спрашиваем ИИ короткий текст для роликов
    text_data = await get_ai_titles(title, price)
    
    # 1. Загружаем чистый объект в первую вкладку таблицы
    save_object_to_sheet(now_str, "Парсер (Крыша)", title, price, phone, "Хозяин", "Проверить документы")
    
    # 2. Загружаем полный развернутый сценарий во вторую вкладку таблицы
    full_scenario = f"ХУК: {text_data['hook']}\nТекст для Reels: Обзор квартиры {title} за {price}. Отличный ремонт, документы готовы. Звоните!"
    save_content_to_sheet(now_str, f"{title} ({price})", "YouTube Shorts / TikTok", full_scenario)
    
    # 3. БОТ САМ ПРЕВРАЩАЕТ ФОТО В ГОТОВЫЙ ВЕРТИКАЛЬНЫЙ РОЛИК С ТИТРАМИ
    video_file = create_auto_video(local_images, text_data["hook"], text_data["sub"])
    
    # 4. Бот присылает готовый медиафайл тебе на телефон (без публикации в каналы!)
    if video_file and os.path.exists(video_file):
        with open(video_file, 'rb') as video:
            await bot.send_video(
                chat_id=ADMIN_ID, 
                video=video, 
                caption=f"🎬 *Авто-контент сгенерирован!*\n\n🏠 {title}\n💰 Цена: {price}\n📞 Контакт: {phone}\n\nВсе данные уже в Google Таблице!",
                parse_mode="Markdown"
            )
        os.remove(video_file)
        
    for img in local_images: 
        if os.path.exists(img): os.remove(img)

# ══════════════════════════════════════════════════
# ОБРАБОТКА ВХОДЯЩИХ КЛИЕНТОВ (ПЕРЕГОВОРЫ)
# ══════════════════════════════════════════════════
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == ADMIN_ID: return # Тебя бот игнорирует

    text = update.message.text or ""
    username = update.effective_user.username or f"id_{user_id}"
    
    # Ответ заглушкой (или можно подключить полноценный диалог Gemini)
    await update.message.reply_text("Здравствуйте! Принял ваш запрос по квартире. Оставьте ваш номер телефона, я проверю актуальность и сразу свяжусь с вами. 🏢")

    # Проверяем регуляркой номер телефона
    phone_match = re.search(r'(\+?7|8)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}', text)
    if phone_match:
        phone = phone_match.group(0)
        # Кладем клиента в Таблицу (Вкладка 1)
        save_object_to_sheet(datetime.now().strftime("%d.%m.%Y %H:%M"), "Чат-бот (Покупатель)", "Клиент с рекламы", "—", phone, f"@{username}", f"Писал: {text}")
        # Пишем тебе в личку
        await ctx.bot.send_message(ADMIN_ID, f"🔥 *Новый клиент в таблице!*\n👤 Юзер: @{username}\n📞 Телефон: {phone}")

# ══════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ И СОВМЕСТНЫЙ ЗАПУСК
# ══════════════════════════════════════════════════
async def run_bot_and_web():
    app = Application.builder().token(BOT_TOKEN).build()

    # Таймер парсера и монтажера (каждые 30 минут)
    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=30, args=[app.bot])
    scheduler.start()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("🤖 Телеграм-модуль успешно запущен.")

def main():
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot_and_web())
    
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(api_app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
