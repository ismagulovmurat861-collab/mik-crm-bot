import os
import logging
import asyncio
import json
import datetime as dt
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

DAILY_LIMIT = 15
DAILY_LIMIT_CACHE = {}
PARSED_CACHE = set()

api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK Bot - Test Mode"}

genai.configure(api_key=GEMINI_KEY)

def get_sheet(sheet_name="baza"):
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    except Exception as e:
        log.error(f"Sheets Error: {e}")
        return None

# ===================== ПАРСЕР =====================
async def parse_krisha():
    log.info("🚀 [ПАРСЕР] Старт проверки Krisha.kz")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get("https://krisha.kz/prodazha/kvartiry/astana/?page=1", headers=headers, timeout=20)
        log.info(f"[ПАРСЕР] Статус: {resp.status_code}")

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.find_all('div', class_='a-card')
        log.info(f"[ПАРСЕР] Найдено карточек: {len(cards)}")

        for card in cards[:10]:
            link = card.find('a', href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            price_tag = card.find('div', class_='price')
            price = price_tag.get_text(strip=True) if price_tag else ""
            
            log.info(f"[ПАРСЕР] Найден: {title[:60]}... | {price}")
            
            full_url = "https://krisha.kz" + link['href']
            obj_id = link['href'].split('/')[-1]

            if obj_id not in PARSED_CACHE:
                PARSED_CACHE.add(obj_id)
                return {"id": obj_id, "title": title, "price": price, "url": full_url}
    except Exception as e:
        log.error(f"[ПАРСЕР] Ошибка: {e}")
    return None

# ===================== ОСНОВНАЯ ЗАДАЧА =====================
async def auto_production_job(bot):
    log.info("=== [ЗАДАЧА] Запуск auto_production_job ===")
    
    obj = await parse_krisha()
    if not obj:
        log.info("=== [ЗАДАЧА] Подходящих объектов не найдено ===")
        return

    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")

    sheet = get_sheet("baza")
    if sheet:
        try:
            row = [now_str, "Krisha.kz", obj["title"], obj["price"], "₸", obj["url"], "Астана", "", "", "", "", "", "Новый от бота", "Новый"]
            sheet.append_row(row)
            log.info("🎉 [УСПЕХ] Объект записан в базу!")
        except Exception as e:
            log.error(f"[ОШИБКА] Запись в базу: {e}")

# ===================== ЗАПУСК =====================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=8, args=[app.bot])   # каждые 8 минут
    scheduler.start()

    # Немедленный первый запуск
    asyncio.create_task(auto_production_job(app.bot))

    log.info("🤖 Бот запущен + принудительный первый парсинг")

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот активен. Парсер запущен.")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Записано.")))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
