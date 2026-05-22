import os
import logging
import asyncio
import datetime as dt
import threading
import re
import random
import time
import json
from fastapi import FastAPI
from google import genai
from google.genai import types
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

MY_PHONE_NUMBER = "+77058060781" 
DAILY_LIMIT_CACHE = {}

AGENT_SYSTEM = (
    f"Ты — ИИ-брокер компании MiK Real Estate в Астане. "
    f"КРИТИЧЕСКОЕ ПРАВИЛО: Пиши СТРОГО кратко, всего 1-2 коротких предложения за раз! Без списков.\n\n"
    f"СЦЕНАРИЙ ОБЩЕНИЯ:\n"
    f"1. Если клиент только зашел, спроси: 'Вы хотите КУПИТЬ недвижимость (ипотека / наличные) или СНЯТЬ?'\n"
    f"2. Если клиент ищет коммерцию или конкретные параметры, ответь: 'Принял ваш запрос. Я сверюсь с нашей базой и свяжусь с вами. Оставьте ваш телефон или напишите руководителю: {MY_PHONE_NUMBER}.'\n"
    f"3. Твоя цель — вытащить телефон или перевести на твой номер."
)

api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK CRM Бот в режиме самоисправления ошибок активен"}

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

# АВТОНОМНАЯ ФУНКЦИЯ ЗАПИСИ С АВТО-ИСПРАВЛЕНИЕМ ОШИБОК
def sync_save_to_sheet(date_str, source, title, price, phone, district, category):
    # 1. Авто-исправление кривого форматирования JSON из переменных окружения
    try:
        cleaned_json = GOOGLE_CREDS_JSON.strip()
        # Если перенос строк сломался в Render, чиним базовые символы
        if "\\n" in cleaned_json and not '\n' in cleaned_json:
            cleaned_json = cleaned_json.replace("\\n", "\n")
        creds_dict = json.loads(cleaned_json)
    except Exception as e:
        log.error(f"🚨 Самоисправление: Не удалось починить JSON ключа Google: {e}")
        return

    # 2. Авто-исправление названий вкладок (маппинг опечаток)
    category_map = {
        "квартира": "квартиры", "квартиры": "квартиры",
        "дом": "дома", "дома": "дома",
        "коттедж": "коттеджи", "коттеджи": "коттеджи",
        "земля": "земля", "участок": "земля",
        "client": "clients", "clients": "clients", "клиент": "clients"
    }
    
    target_sheet = category_map.get(category.lower().strip(), "clients")

    # 3. Авто-повтор при сбоях сети (3 попытки с паузой)
    for attempt in range(1, 4):
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            client = gspread.authorize(creds)
            
            sheet = client.open_by_key(SPREADSHEET_ID).worksheet(target_sheet)
            
            if target_sheet == "clients":
                sheet.append_row([date_str, source, title, price, phone])
            else:
                sheet.append_row([date_str, source, district, title, price, phone])
                
            log.info(f"💾 Данные успешно ушли в CRM (Лист: {target_sheet}) с попытки {attempt}")
            return # Выходим из цикла, если всё записалось успешно
        except Exception as e:
            log.warning(f"⚠️ Попытка {attempt} записать в таблицу сорвалась: {e}. Пробую еще раз...")
            time.sleep(2) # Даем системе 2 секунды "прийти в себя"
            
    log.error("🚨 Все 3 попытки фоновой записи провалились. Проверьте доступы к таблице.")

def safe_async_save(date_str, source, title, price, phone, district="Не указан", category="clients"):
    threading.Thread(
        target=sync_save_to_sheet, 
        args=(date_str, source, title, price, phone, district, category), 
        daemon=True
    ).start()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if update.message.from_user.is_bot: return
    
    user_text = update.message.text
    chat_id = update.message.chat_id
    username = update.message.from_user.username or f"id_{chat_id}"
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")

    # Включаем калькулятор ТОЛЬКО по строгим триггерам
    trigger_words = ["ипотека", "просчитать", "калькулятор", "рассчитать", "расчет"]
    wants_calculator = any(word in user_text.lower() for word in trigger_words)
    
    if wants_calculator:
        numbers = [int(s) for s in re.findall(r'\d+', user_text.replace(" ", "").replace("млн", "000000"))]
        if len(numbers) >= 1 and numbers[0] >= 1000000:
            price = numbers[0]
            down = numbers[1] if len(numbers) >= 2 else int(price * 0.20)
            loan, monthly = calculate_mortgage(price, down, rate_annual=17, years=20)
            
            if loan > 0:
                reply = (
                    f"🧮 **Ипотечный экспресс-расчет:**\n\n"
                    f"• Стоимость жилья: {price:,} ₸\n"
                    f"• Первоначальный взнос: {down:,} ₸\n"
                    f"• Сумма кредита: {loan:,} ₸\n"
                    f"• Ежемесячный платеж (на 20 лет): **~{monthly:,} ₸/мес**\n\n"
                    f"Я передал эти параметры руководителю. Он проверит подходящие варианты в нашей базе новостроек и свяжется с вами. Напишите ваш телефон или свяжитесь напрямую: {MY_PHONE_NUMBER}"
                )
                await update.message.reply_text(reply, parse_mode="Markdown")
                safe_async_save(now_str, f"Расчет (@{username})", f"Жилье: {price} Взнос: {down}", f"Платеж: {monthly}", f"ID: {chat_id}", category="clients")
                await context.bot.send_message(ADMIN_ID, f"🏢 **Запрос расчета!**\n👤 @{username}\n💰 Бюджет: {price:,} ₸")
                return

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
        reply_text = f"Принял ваш запрос! Оставьте ваш номер телефона для связи или напишите мне на WhatsApp: {MY_PHONE_NUMBER}"
        
    await update.message.reply_text(reply_text)
    safe_async_save(now_str, f"Чат-бот (@{username})", user_text, "Консультация", f"ID: {chat_id}", category="clients")
    try:
        await context.bot.send_message(ADMIN_ID, f"🔥 **Новый лид!**\n👤 @{username}\n💬 Текст: {user_text}")
    except: pass

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    await update.message.reply_text(
        "Приветствую! Я ИИ-помощник MiK Real Estate в Астане. 🏠\n\n"
        "Вы планируете КУПИТЬ недвижимость (в ипотеку / наличные) или СНЯТЬ?"
    )

def main():
    def run_uvicorn():
        port = int(os.getenv("PORT", 10000))
        import uvicorn
        uvicorn.run(api_app, host="0.0.0.0", port=port)
        
    threading.Thread(target=run_uvicorn, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == "__main__":
    main()
