import os
import json
import logging
import threading
import time
from telegram.ext import Application, MessageHandler, filters
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from fastapi import FastAPI
import uvicorn

# Настройка логов
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Инициализация веб-сервера для Render
app = FastAPI()
@app.get("/")
def health(): return {"status": "ok"}

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ФУНКЦИЯ ЗАПИСИ (Исправлена работа с JSON)
def save_to_crm(data):
    try:
        # Берем JSON из переменной окружения
        creds_json = os.getenv("GOOGLE_CREDS_JSON")
        creds_dict = json.loads(creds_json)
        
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # ЗАМЕНИ "Косоланов" НА ТОЧНОЕ ИМЯ ЛИСТА
        sheet = client.open_by_key(os.getenv("SPREADSHEET_ID")).worksheet("Косоланов")
        sheet.append_row(data)
        log.info("УСПЕХ: Запись в базу")
    except Exception as e:
        log.error(f"ОШИБКА БАЗЫ: {e}")

# Основная логика
async def handle_msg(update, context):
    text = update.message.text
    # Записываем в базу всё, что пишет клиент
    save_to_crm([time.ctime(), update.message.from_user.username, text])
    await update.message.reply_text("Заявка принята!")

if __name__ == "__main__":
    # Запуск сервера в фоне
    threading.Thread(target=run_server, daemon=True).start()
    
    # Запуск бота
    app = Application.builder().token(os.getenv("BOT_TOKEN")).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_msg))
    app.run_polling()
