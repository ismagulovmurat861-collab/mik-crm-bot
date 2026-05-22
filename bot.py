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
from google import genai
from google.genai import types
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

DAILY_LIMIT = 8
DAILY_LIMIT_CACHE = {}
PARSED_CACHE = set()

api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK CRM Bot — Debugging mode"}

def get_sheet(sheet_name="baza"):
    try:
        log.info(f"Попытка подключения к Google Sheets: {sheet_name}")
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
        log.info(f"✅ Успешно подключено к вкладке: {sheet_name}")
        return sheet
    except Exception as e:
        log.error(f"❌ Ошибка подключения к Google Sheets: {e}")
        return None

# ===================== ПАРСЕР =====================
async def parse_krisha():
    try:
        log.info("🔍 Запуск парсинга Krisha.kz...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = "https://krisha.kz/prodazha/kvartiry/astana/?page=1"
        
        resp = requests.get(url, headers=headers, timeout=20)
        log.info(f"Статус ответа: {resp.status_code}")

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.find_all('div', class_='a-card')

        log.info(f"Найдено карточек: {len(cards)}")

        for card in cards[:10]:
            try:
                link = card.find('a', href=True)
                if not link:
                    continue
                title = link.get_text(strip=True)
                price_tag = card.find('div', class_='price')
                price = price_tag.get_text(strip=True) if price_tag else "Цена по запросу"
                full_url = "https://krisha.kz" + link['href']
                obj_id = link['href'].split('/')[-1]

                if obj_id in PARSED_CACHE:
                    continue

                log.info(f"Найден новый объект: {title}")
                return {
                    "id": obj_id,
                    "title": title,
                    "price": price,
                    "url": full_url
                }
            except Exception as e:
                log.warning(f"Ошибка обработки карточки: {e}")
                continue
    except Exception as e:
        log.error(f"Критическая ошибка парсера: {e}")
    return None

# ===================== ОСНОВНАЯ ФУНКЦИЯ =====================
async def auto_production_job(bot):
    log.info("=== Запуск auto_production_job ===")
    
    today = dt.date.today().strftime("%Y-%m-%d")
    if today not in DAILY_LIMIT_CACHE:
        DAILY_LIMIT_CACHE[today] = 0

    if DAILY_LIMIT_CACHE[today] >= DAILY_LIMIT:
        log.info("Лимит объектов на сегодня исчерпан")
        return

    obj = await parse_krisha()
    if not obj:
        log.info("Новых объектов не найдено")
        return

    PARSED_CACHE.add(obj["id"])
    log.info(f"Объект взят в работу: {obj['title']}")

    # === ЗАПИСЬ В БАЗУ ===
    sheet = get_sheet("baza")
    if sheet:
        try:
            now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
            row = [now_str, "Krisha.kz", obj["title"], obj["price"], "₸", obj["url"], "Астана", "", "", "", "", "", "Новый объект", "Новый"]
            sheet.append_row(row)
            log.info("✅ Данные успешно записаны в базу!")
        except Exception as e:
            log.error(f"Ошибка записи в Google Sheets: {e}")

    DAILY_LIMIT_CACHE[today] += 1

# ===================== ЗАПУСК =====================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=15, args=[app.bot])  # уменьшил интервал для теста
    scheduler.start()

    log.info("🤖 Бот запущен. Ждём задачи...")

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот работает. Парсер запущен.")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Запрос принят.")))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
