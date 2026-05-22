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
    return {"status": "Debug Mode Active"}

genai.configure(api_key=GEMINI_KEY)

def get_sheet(sheet_name="baza"):
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    except Exception as e:
        log.error(f"Google Sheets Error: {e}")
        return None

# ===================== УПРОЩЁННЫЙ ПАРСЕР =====================
async def parse_krisha():
    log.info("🚀 Старт парсинга Krisha.kz")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = "https://krisha.kz/prodazha/kvartiry/astana/?page=1"
        
        resp = requests.get(url, headers=headers, timeout=25)
        log.info(f"Статус ответа: {resp.status_code}")

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.find_all('div', class_='a-card')

        log.info(f"Всего карточек на странице: {len(cards)}")

        for card in cards[:20]:   # берём больше для теста
            try:
                link = card.find('a', href=True)
                if not link: 
                    continue

                title = link.get_text(strip=True)
                price_tag = card.find('div', class_='price')
                price = price_tag.get_text(strip=True) if price_tag else ""

                log.info(f"Проверяем объект: {title[:60]}... | Цена: {price}")

                full_url = "https://krisha.kz" + link['href']
                obj_id = link['href'].split('/')[-1]

                if obj_id in PARSED_CACHE:
                    continue

                # Очень мягкая фильтрация для теста
                if "аренда" in title.lower() or "сдам" in title.lower():
                    continue

                PARSED_CACHE.add(obj_id)
                log.info(f"✅ ВЫБРАН объект: {title[:70]}")
                return {
                    "id": obj_id,
                    "title": title,
                    "price": price,
                    "url": full_url
                }
            except Exception as e:
                log.warning(f"Ошибка при обработке карточки: {e}")
                continue

        log.info("Подходящих объектов не найдено на этой странице")
    except Exception as e:
        log.error(f"Критическая ошибка парсера: {e}")
    return None

# ===================== ОСНОВНАЯ ФУНКЦИЯ =====================
async def auto_production_job(bot):
    log.info("=== ЗАПУСК auto_production_job ===")
    
    today = dt.date.today().strftime("%Y-%m-%d")
    if today not in DAILY_LIMIT_CACHE:
        DAILY_LIMIT_CACHE[today] = 0

    if DAILY_LIMIT_CACHE[today] >= DAILY_LIMIT:
        log.info("Лимит на сегодня исчерпан")
        return

    obj = await parse_krisha()
    if not obj:
        log.info("Ничего не найдено в этот раз")
        return

    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")

    sheet = get_sheet("baza")
    if sheet:
        try:
            row = [now_str, "Krisha.kz", obj["title"], obj["price"], "₸", obj["url"], 
                   "Астана", "", "", "", "", "", "Новый объект от бота", "Новый"]
            sheet.append_row(row)
            log.info("🎉 УСПЕШНО ЗАПИСАНО В БАЗУ!")
        except Exception as e:
            log.error(f"Ошибка записи в таблицу: {e}")

    DAILY_LIMIT_CACHE[today] += 1

# ===================== ЗАПУСК =====================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=10, args=[app.bot])  # 10 минут для теста
    scheduler.start()

    log.info("🤖 Bot запущен с максимальным логированием")

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот работает. Ждём объекты...")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Записал.")))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
