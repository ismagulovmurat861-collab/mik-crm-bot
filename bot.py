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
import uvicorn

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

# ===================== FASTAPI =====================
api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK Bot v2.4 - OK", "time": dt.datetime.now().isoformat()}

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
    log.info("🚀 Парсер Krisha запущен")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get("https://krisha.kz/prodazha/kvartiry/astana/?page=1", headers=headers, timeout=20)
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.find_all('div', class_='a-card')
        log.info(f"Найдено карточек: {len(cards)}")

        for card in cards[:15]:
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
                log.info(f"✅ Взят объект: {title[:60]}...")
                return {"id": obj_id, "title": title, "price": price, "url": full_url}
    except Exception as e:
        log.error(f"Парсер ошибка: {e}")
    return None

# ===================== ЗАДАЧА =====================
async def auto_production_job(bot):
    log.info("=== Запуск парсера ===")
    obj = await parse_krisha()
    if not obj:
        log.info("Объектов не найдено")
        return

    sheet = get_sheet("baza")
    if sheet:
        now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
        row = [now_str, "Krisha.kz", obj["title"], obj["price"], "₸", obj["url"], "Астана", "", "", "", "", "", "Новый от бота", "Новый"]
        sheet.append_row(row)
        log.info("🎉 Записано в базу!")

# ===================== ЗАПУСК =====================
async def main():
    # Запуск Telegram Bot
    tg_app = Application.builder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот активен")))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Записано")))

    # Планировщик
    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=10, args=[tg_app.bot])
    scheduler.start()

    # Первый запуск сразу
    asyncio.create_task(auto_production_job(tg_app.bot))

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()

    log.info("🤖 Bot + Web Server запущен")

    # Запуск FastAPI
    config = uvicorn.Config(api_app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
