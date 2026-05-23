import os
import logging
from telegram.ext import Application, MessageHandler, filters
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from fastapi import FastAPI
import uvicorn
import threading
import google.generativeai as genai

# Настройка
logging.basicConfig(level=logging.INFO)
genai.configure(api_key="ВАШ_API_KEY_GEMINI") # Вставь сюда ключ от Gemini, если используешь модель

# Веб-сервер для Render (чтобы не убивал порт)
app = FastAPI()
@app.get("/")
def health(): return {"status": "ok"}

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

def save_to_google(row_data):
    try:
        # Авторизация строго через файл из Secret Files
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key("ВАШ_ID_ТАБЛИЦЫ").worksheet("Косоланов")
        sheet.append_row(row_data)
    except Exception as e:
        print(f"Ошибка базы: {e}")

async def handle_msg(update, context):
    save_to_google([update.message.text])
    await update.message.reply_text("Записал в Косоланов!")

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    app_bot = Application.builder().token("ВАШ_BOT_TOKEN").build()
    app_bot.add_handler(MessageHandler(filters.TEXT, handle_msg))
    app_bot.run_polling()
