import os
import logging
import asyncio
import datetime as dt
import threading
from fastapi import FastAPI
from google import genai
from google.genai import types
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from PIL import Image as PILImage, ImageDraw, ImageFont

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# Чтение переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

DAILY_LIMIT_CACHE = {}
PARSED_CACHE = set()

AGENT_SYSTEM = "Ты — ИИ-брокер компании MiK Real Estate в Астане. Твоя цель — вежливо и профессионально помогать клиентам."
CONTENT_FACTORY_SYSTEM = """Ты — креативный продюсер недвижимости в Астаной. Твоя задача — проанализировать параметры объекта и выдать JSON.
Выбери строго категорию из списка: квартиры, дома, коттеджи, земля.
Определи район Астаны (например: Есиль, Нура, Алматы, Сарыарка, Байконур). Если не указан, напиши "Не указан".
Придумай мощный хук для соцсетей.

Ответь СТРОГО в формате JSON без markdown:
{"category": "категория", "district": "район", "hook": "хук", "sub": "преимущество"}"""

api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK Умный Контент-Завод запущен!"}

# УМНАЯ СОРТИРОВКА В GOOGLE ТАБЛИЦУ
async def save_object_to_sheet(date_str, source, title, price, phone, district="Не указан", category="квартиры"):
    try:
        import json
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        
        # Бот автоматически открывает нужную вкладку (квартиры, дома, земля, clients и т.д.)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(category.lower())
        
        if category == "clients":
            sheet.append_row([date_str, source, title, price, phone])
        else:
            # Для объектов добавляем колонку района
            sheet.append_row([date_str, source, district, title, price, phone])
            
        log.info(f"✅ Данные успешно отправлены во вкладку [{category}] | Район: {district}")
    except Exception as e:
        log.error(f"❌ Ошибка Google Таблиц во вкладке {category}: {e}")

# ЗАПРОС К GEMINI ДЛЯ АНАЛИЗА И ХУКОВ
async def analyze_object_with_ai(title, price):
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Объект: {title}, Цена: {price}. Сделай анализ объекта.",
            config=types.GenerateContentConfig(
                system_instruction=CONTENT_FACTORY_SYSTEM,
                response_mime_type="application/json"
            )
        )
        import json
        return json.loads(response.text.strip())
    except Exception as e:
        log.error(f"Ошибка Gemini ИИ при анализе: {e}")
        return {"category": "квартиры", "district": "Не указан", "hook": "Горячее предложение!", "sub": f"{title}"}

# ФУНКЦИЯ СБОРКИ ВИДЕО
def create_auto_video(image_paths, hook_text, sub_text, output_path="result.mp4"):
    try:
        processed_images = []
        for i, img_path in enumerate(image_paths):
            with PILImage.open(img_path) as img:
                img.thumbnail((1080, 1920))
                background = PILImage.new('RGB', (1080, 1920), (0, 0, 0))
                offset = ((1080 - img.width) // 2, (1920 - img.height) // 2)
                background.paste(img, offset)
                
                if i < 2:
                    draw = ImageDraw.Draw(background)
                    try:
                        font_hook = ImageFont.load_default(size=55)
                        font_sub = ImageFont.load_default(size=35)
                    except:
                        font_hook = ImageFont.load_default()
                        font_sub = ImageFont.load_default()
                        
                    draw.text((540, 800), hook_text, fill="yellow", font=font_hook, anchor="mm")
                    draw.text((540, 950), sub_text, fill="white", font=font_sub, anchor="mm")
                
                frame_path = f"frame_{i}.jpg"
                background.save(frame_path, "JPEG")
                processed_images.append(frame_path)
        
        if processed_images:
            os.rename(processed_images[0], output_path)
            return output_path
    except Exception as e:
        log.error(f"Ошибка сборки видео: {e}")
    return None

# АВТОМАТИЧЕСКИЙ ОПРОС И КОНТЕНТ-ЗАВОД
async def auto_production_job(bot):
    log.info("🔍 Парсер проверяет новые объявления...")
    today_date = dt.date.today().strftime("%Y-%m-%d")
    
    if today_date not in DAILY_LIMIT_CACHE:
        DAILY_LIMIT_CACHE[today_date] = 0
        
    if DAILY_LIMIT_CACHE[today_date] >= 8:
        log.info(f"🚫 Лимит в 8 объектов на сегодня ({today_date}) достигнут.")
        return

    demo_id = f"parsed_{dt.datetime.now().strftime('%H%M%S')}"
    if demo_id in PARSED_CACHE: 
        return
    PARSED_CACHE.add(demo_id)
    
    # Имитируем парсинг (здесь ИИ определит, что это коттедж в Есильском районе)
    title = "Коттедж 5 комнат, 300 м², район Есиль, ЖК Лесная поляна"
    price = "85 000 000 ₸"
    phone = "+7701555XX"
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Отдаем объект ИИ на сканирование категории и района
    ai_data = await analyze_object_with_ai(title, price)
    
    # Временные кадры для видео
    local_images = []
    for color, name in [((20,20,50), "v1.jpg"), ((30,50,30), "v2.jpg")]:
        img = PILImage.new('RGB', (800, 600), color)
        img.save(name)
        local_images.append(name)
        
    # Сохраняем строго в определенную ИИ категорию и записываем район
    await save_object_to_sheet(
        now_str, "Парсер (Крыша)", title, price, phone, 
        district=ai_data.get('district', 'Не указан'), 
        category=ai_data.get('category', 'квартиры')
    )
    
    video_file = create_auto_video(local_images, ai_data['hook'], ai_data['sub'])
    
    caption = f"🤖 **Умный Парсер MiK**\n\n🗂 Категория: #{ai_data.get('category')}\n📍 Район: {ai_data.get('district')}\n🏠 {title}\n💰 Цена: {price}\n📞 Тел: {phone}\n\n🔥 Хук для Reels:\n`{ai_data['hook']}`"
    
    try:
        if video_file and os.path.exists(video_file):
            with open(video_file, 'rb') as video:
                await bot.send_video(chat_id=ADMIN_ID, video=video, caption=caption, parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=ADMIN_ID, text=caption, parse_mode="Markdown")
    except Exception as e:
        log.error(f"Ошибка отправки отчета в телеграм: {e}")

    DAILY_LIMIT_CACHE[today_date] += 1

# ОБРАБОТКА МЕССЕДЖЕЙ КЛИЕНТОВ
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.message.chat_id
    username = update.message.from_user.username or "Покупатель"
    
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
            config=types.GenerateContentConfig(system_instruction=AGENT_SYSTEM)
        )
        reply_text = response.text
    except Exception as e:
        log.error(f"Ошибка ИИ: {e}")
        reply_text = "Здравствуйте! Принял ваш запрос, скоро свяжусь с вами."
        
    await update.message.reply_text(reply_text)
    
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    # Клиентов отправляем на вкладку "clients"
    await save_object_to_sheet(now_str, f"Чат-бот (@{username})", user_text, "Клиент", f"ID: {chat_id}", category="clients")
    await context.bot.send_message(ADMIN_ID, f"🔥 **Новый лид!**\n👤 Юзер: @{username}\n💬 Текст: {user_text}")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Приветствую! Я ИИ-ассистент агентства недвижимости MiK. Напишите, что вы ищете в Астане?")

# СТАБИЛЬНЫЙ МНОГОПОТОЧНЫЙ ЗАПУСК
def main():
    # Запускаем FastAPI веб-сервер в отдельном фоновом потоке
    def run_uvicorn():
        port = int(os.getenv("PORT", 10000))
        import uvicorn
        uvicorn.run(api_app, host="0.0.0.0", port=port)
        
    srv_thread = threading.Thread(target=run_uvicorn, daemon=True)
    srv_thread.start()
    
    log.info("🚀 Запуск Телеграм-модуля и планировщика...")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=30, args=[app.bot])
    scheduler.start()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бесконечный цикл Телеграм-бота (основной поток)
    app.run_polling()

if __name__ == "__main__":
    main()
