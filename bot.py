import os
import logging
import asyncio
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
    return {"status": "MiK Bot - Running", "time": str(dt.datetime.now())}

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

async def parse_krisha():
    log.info("🚀 ПАРСЕР: Начало работы")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get("https://krisha.kz/prodazha/kvartiry/astana/?page=1", headers=headers, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.find_all('div', class_='a-card')
        
        log.info(f"ПАРСЕР: Найдено карточек = {len(cards)}")

        for card in cards[:12]:
            link = card.find('a', href=True)
            if not link: continue
            title = link.get_text(strip=True)
            price_tag = card.find('div', class_='price')
            price = price_tag.get_text(strip=True) if price_tag else ""

            if "аренда" in title.lower() or "сдам" in title.lower():
                continue

            full_url = "https://krisha.kz" + link['href']
            obj_id = link['href'].split('/')[-1]

            if obj_id not in PARSED_CACHE:
                PARSED_CACHE.add(obj_id)
                log.info(f"✅ ПАРСЕР: Выбран объект → {title[:60]}...")
                return {"id": obj_id, "title": title, "price": price, "url": full_url}
    except Exception as e:
        log.error(f"ПАРСЕР: Ошибка {e}")
    return None

async def auto_production_job(bot):
    log.info("=== ЗАПУСК ПАРСЕРА ===")
    obj = await parse_krisha()
    if not obj:
        log.info("=== Ничего не найдено ===")
        return

    sheet = get_sheet("baza")
    if sheet:
        now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
        row = [now_str, "Krisha.kz", obj["title"], obj["price"], "₸", obj["url"], "Астана", "", "", "", "", "", "Новый от бота", "Новый"]
        sheet.append_row(row)
        log.info("🎉 УСПЕШНО ЗАПИСАНО В БАЗУ!")

# ===================== ЗАПУСК =====================
async def main():
    tg_app = Application.builder().token(BOT_TOKEN).build()

    # Планировщик
    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=10, args=[tg_app.bot])
    scheduler.start()

    # Немедленный запуск
    asyncio.create_task(auto_production_job(tg_app.bot))

    log.info("🤖 Бот запущен. Первый парсинг запущен принудительно.")

    tg_app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот работает. Парсер активен.")))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Записано.")))

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
