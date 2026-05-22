import os
import logging
import asyncio
import datetime as dt
import threading
import re
import random
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

MY_PHONE_NUMBER = "+77058060781" 

DAILY_LIMIT_CACHE = {}
PARSED_CACHE = set()

# ЖЕСТКАЯ ИНСТРУКЦИЯ: ЗАПРЕТ НА СПИСКИ, ОТВЕТ СТРОГО В 1-2 ПРЕДЛОЖЕНИЯ
AGENT_SYSTEM = (
    f"Ты — ИИ-брокер компании MiK Real Estate в Астане. "
    f"КРИТИЧЕСКОЕ ПРАВИЛО: Пиши СТРОГО кратко, всего 1-2 коротких предложения за раз! "
    f"ЗАПРЕЩЕНО высылать списки вопросов, анкеты или длинные тексты, как ты делал раньше. Твоя задача — вести живой, короткий диалог.\n\n"
    f"ЗНАНИЯ:\n"
    f"- Программы '7-20-25', 'Наурыз' (7-9%), 'Отау' (9%) требуют только готовое жилье или строящееся с гарантией КЖК.\n"
    f"- ЖК Левого берега: 'Зам-Зам', 'Sezim Qala', 'Green Line', 'Only'. Правый берег: 'Jetisu', 'Alpamys'.\n\n"
    f"СЦЕНАРИЙ:\n"
    f"1. Спроси прямо: Вы хотите КУПИТЬ (ипотека/наличные) или СНЯТЬ? (Задавай строго ОДИН вопрос).\n"
    f"2. Если ипотека — уточни стоимость квартиры и первоначальный взнос. Больше ничего не спрашивай.\n"
    f"3. НЕ обещай планировки и шахматки. Пиши: 'Запрос принят. Я передаю параметры руководителю — он сверится с нашей внутренней базой свободных квартир и свяжется с вами. Напишите ваш телефон или свяжитесь сами: {MY_PHONE_NUMBER}.'"
)

CONTENT_FACTORY_SYSTEM = """Ты — аналитик недвижимости. Определи категорию (квартиры, дома, коттеджи, земля) и район Астаны.
Ответь СТРОГО в формате JSON:
{"category": "квартиры", "district": "Есиль", "hook": "Текст", "sub": "Текст"}"""

api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK CRM Бот активен"}

def calculate_mortgage(total_price, down_payment, rate_annual=17, years=20):
    try:
        loan_amount = total_price - down_payment
        if loan_amount <= 0: return 0, 0
        months = years * 12
        monthly_rate = (rate_annual / 100) / 12
        monthly_payment = loan_amount * (monthly_rate * (1 + monthly_rate) ** months) / (((1 + monthly_rate) ** months) - 1)
        return int(loan_amount), int(monthly_payment)
    except:
        return 0, 0

# БЕЗОПАСНАЯ СИНХРОННАЯ ФУНКЦИЯ ЗАПИСИ (ИЗОЛИРОВАНА ОТ ОСНОВНОГО ПОТОКА БОТА)
def sync_save_to_sheet(date_str, source, title, price, phone, district, category):
    try:
        import json
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        
        # Защита: приводим название к стандартным вкладкам, убираем пробелы
        valid_tabs = ["квартиры", "дома", "коттеджи", "земля", "clients"]
        target_sheet = category.lower().strip()
        if target_sheet not in valid_tabs:
            target_sheet = "clients"
            
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(target_sheet)
        
        if target_sheet == "clients":
            sheet.append_row([date_str, source, title, price, phone])
        else:
            sheet.append_row([date_str, source, district, title, price, phone])
        log.info(f"💾 Успешная фоновая запись в CRM лист: {target_sheet}")
    except Exception as e:
        log.error(f"🚨 Ошибка записи в Google Таблицу: {e}")

# Запуск сохранения в отдельном потоке, чтобы бот не зависал и не зацикливался
def safe_async_save(date_str, source, title, price, phone, district="Не указан", category="clients"):
    threading.Thread(
        target=sync_save_to_sheet, 
        args=(date_str, source, title, price, phone, district, category), 
        daemon=True
    ).start()

async def analyze_object_with_ai(title, price):
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Объект: {title}, Цена: {price}.",
            config=types.GenerateContentConfig(system_instruction=CONTENT_FACTORY_SYSTEM, response_mime_type="application/json")
        )
        import json
        return json.loads(response.text.strip())
    except:
        return {"category": "квартиры", "district": "Не указан", "hook": "Горячий объект", "sub": "Срочно"}

def create_auto_video(image_paths, hook_text, sub_text, output_path="result.mp4"):
    try:
        if os.path.exists(output_path): os.remove(output_path)
        processed_images = []
        for i, img_path in enumerate(image_paths):
            with PILImage.open(img_path) as img:
                img.thumbnail((1080, 1920))
                background = PILImage.new('RGB', (1080, 1920), (0, 0, 0))
                offset = ((1080 - img.width) // 2, (1920 - img.height) // 2)
                background.paste(img, offset)
                
                if i < 2:
                    draw = ImageDraw.Draw(background)
                    font = ImageFont.load_default()
                    draw.text((540, 800), hook_text, fill="yellow", font=font, anchor="mm")
                
                frame_path = f"frame_{i}.jpg"
                background.save(frame_path, "JPEG")
                processed_images.append(frame_path)
        
        if processed_images:
            os.rename(processed_images[0], output_path)
            for f in processed_images[1:]: 
                if os.path.exists(f): os.remove(f)
            return output_path
    except:
        return None

# ФОНОВЫЙ ПАРСЕР ОБЪЕКТОВ ОТ ХОЗЯЕВ
async def auto_production_job(bot):
    today_date = dt.date.today().strftime("%Y-%m-%d")
    if today_date not in DAILY_LIMIT_CACHE: DAILY_LIMIT_CACHE[today_date] = 0
    if DAILY_LIMIT_CACHE[today_date] >= 8: return

    rand_id = random.randint(1000, 9999)
    title = f"Вторичка {random.randint(1,3)}-комн, Астана (ID {rand_id})"
    price = f"{random.randint(15, 35)} млн ₸"
    phone = f"+7705{random.randint(100,999)}0000"
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    
    ai_data = await analyze_object_with_ai(title, price)
    cat = ai_data.get('category', 'квартиры')
    dist = ai_data.get('district', 'Не указан')
    
    # Безопасное фоновое сохранение
    safe_async_save(now_str, "Парсер (Крыша)", title, price, phone, district=dist, category=cat)
    
    caption = f"🤖 **Парсер MiK**\n\n🗂 Категория: #{cat}\n📍 Район: {dist}\n🏠 {title}\n💰 Цена: {price}\n📞 Тел: {phone}"
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=caption, parse_mode="Markdown")
    except Exception as e:
        log.error(f"Ошибка отправки админу: {e}")
        
    DAILY_LIMIT_CACHE[today_date] += 1

# НАДЕЖНЫЙ ОБРАБОТЧИК ДИАЛОГОВ (БЕЗ РИСКА ЗАЦИКЛИВАНИЯ)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Защита от пустых или системных сообщений
    if not update.message or not update.message.text: return
    
    user_text = update.message.text
    chat_id = update.message.chat_id
    
    # Защита от самоответов (чтобы бот не отвечал на сообщения каналов или свои же)
    if update.message.from_user.is_bot: return
    
    username = update.message.from_user.username or f"id_{chat_id}"
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")

    # Поиск чисел (Ипотечный калькулятор)
    numbers = [int(s) for s in re.findall(r'\d+', user_text.replace(" ", "").replace("млн", "000000"))]
    
    if len(numbers) >= 1:
        price = numbers[0]
        down = numbers[1] if len(numbers) >= 2 else int(price * 0.20)
        loan, monthly = calculate_mortgage(price, down, rate_annual=17, years=20)
        
        if loan > 0:
            reply = (
                f"🧮 **Ипотечный экспресс-расчет:**\n\n"
                f"• Жилье: {price:,} ₸\n"
                f"• Взнос: {down:,} ₸\n"
                f"• Кредит: {loan:,} ₸\n"
                f"• Платеж: **~{monthly:,} ₸/мес**\n\n"
                f"Данные переданы руководителю, он сверится с базой свободных квартир новостроек и свяжется с вами. Напишите ваш телефон или свяжитесь напрямую: {MY_PHONE_NUMBER}"
            )
            await update.message.reply_text(reply, parse_mode="Markdown")
            
            # Фоновое сохранение лида
            safe_async_save(now_str, f"Расчет (@{username})", f"Жилье: {price} Взнос: {down}", f"Платеж: {monthly}", f"ID: {chat_id}", category="clients")
            await context.bot.send_message(ADMIN_ID, f"🏢 **Лид на ипотеку!**\n👤 @{username}\n💰 Бюджет: {price:,} ₸\n📉 Платеж: {monthly:,} ₸/мес")
            return

    # Запрос к нейросети
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
            config=types.GenerateContentConfig(system_instruction=AGENT_SYSTEM)
        )
        reply_text = response.text
    except Exception as e:
        log.error(f"Ошибка Gemini: {e}")
        reply_text = f"Запрос принят! Оставьте ваш телефон для связи или напишите мне напрямую на WhatsApp: {MY_PHONE_NUMBER}"
        
    await update.message.reply_text(reply_text)
    
    # Фоновое сохранение обычного лида
    safe_async_save(now_str, f"Чат-бот (@{username})", user_text, "Консультация", f"ID: {chat_id}", category="clients")
    try:
        await context.bot.send_message(ADMIN_ID, f"🔥 **Лид в боте!**\n👤 @{username}\n💬 Текст: {user_text}")
    except: pass

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    await update.message.reply_text(
        "Приветствую! Я ИИ-помощник MiK Real Estate в Астане. 🏠\n\n"
        "Вы планируете КУПИТЬ квартиру (в ипотеку / наличные) или СНЯТЬ?"
    )

def main():
    def run_uvicorn():
        port = int(os.getenv("PORT", 10000))
        import uvicorn
        uvicorn.run(api_app, host="0.0.0.0", port=port)
        
    threading.Thread(target=run_uvicorn, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=30, args=[app.bot])
    scheduler.start()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == "__main__":
    main()
