import os
import logging
import asyncio
import datetime as dt
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "Ultra Simple Bot"}

def get_sheet(sheet_name="baza"):
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)
        log.info(f"✅ Успешно подключено к Google Sheets: {sheet_name}")
        return client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    except Exception as e:
        log.error(f"❌ Ошибка Google Sheets: {e}")
        return None

async def test_job():
    log.info("=== ТЕСТОВАЯ ЗАДАЧА ЗАПУЩЕНА ===")
    
    try:
        # Простой запрос на Krisha
        resp = requests.get("https://krisha.kz/prodazha/kvartiry/astana/", 
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        log.info(f"Krisha статус: {resp.status_code}")

        now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
        sheet = get_sheet("baza")
        
        if sheet:
            row = [now_str, "Krisha.kz (тест)", "Тестовый объект", "25000000", "₸", 
                   "https://krisha.kz", "Астана", "", "", "", "", "", "Тест от бота", "Новый"]
            sheet.append_row(row)
            log.info("🎉 ЗАПИСЬ В ТАБЛИЦУ УСПЕШНА!")
            await asyncio.sleep(2)
        else:
            log.error("Не удалось подключиться к таблице")
    except Exception as e:
        log.error(f"Ошибка в test_job: {e}")

# ===================== ЗАПУСК =====================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Запускаем тест сразу
    asyncio.create_task(test_job())

    # И ещё несколько раз
    for i in range(3):
        await asyncio.sleep(180)  # каждые 3 минуты
        asyncio.create_task(test_job())

    log.info("🤖 Ultra Simple Bot запущен. Тесты запущены.")

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот работает. Тест записи запущен.")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Записано.")))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
