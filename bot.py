import os
import logging
import asyncio
import datetime as dt
import json                                      # ← Это было пропущено
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
    return {"status": "Bot Running"}

def get_sheet(sheet_name="baza"):
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
        log.info(f"✅ Подключено к Google Sheets: {sheet_name}")
        return sheet
    except Exception as e:
        log.error(f"❌ Ошибка Google Sheets: {e}")
        return None

async def test_job():
    log.info("=== ТЕСТОВАЯ ЗАДАЧА ЗАПУЩЕНА ===")
    
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    sheet = get_sheet("baza")
    
    if sheet:
        try:
            row = [now_str, "Krisha.kz", "Тестовый объект от бота", "25000000", "₸", 
                   "https://krisha.kz", "Астана", "", "", "", "", "", "Тестовая запись", "Новый"]
            sheet.append_row(row)
            log.info("🎉 УСПЕШНО ЗАПИСАНО В БАЗУ!")
        except Exception as e:
            log.error(f"Ошибка записи: {e}")
    else:
        log.error("Не удалось подключиться к таблице")

# ===================== ЗАПУСК =====================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    asyncio.create_task(test_job())           # сразу
    for _ in range(4):
        await asyncio.sleep(240)              # каждые 4 минуты
        asyncio.create_task(test_job())

    log.info("🤖 Бот запущен с тестовыми записями")

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот работает.")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Записано.")))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
