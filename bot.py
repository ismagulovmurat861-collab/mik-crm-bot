import os
import logging
import threading
import time
import json
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import gspread

# Настройка логов
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# --- CRM ФУНКЦИЯ (В ФОНЕ) ---
def save_to_google_async(data_row, sheet_name="clients"):
    def task():
        try:
            # Исправление: json теперь точно доступен
            creds_data = json.loads(GOOGLE_CREDS_JSON.strip())
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_data, scope)
            client = gspread.authorize(creds)
            client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name).append_row(data_row)
            log.info("Запись в таблицу успешна")
        except Exception as e:
            log.error(f"CRM Error: {e}")
    
    # Запуск в отдельном потоке, чтобы не тормозить бота
    threading.Thread(target=task, daemon=True).start()

# --- ОБРАБОТЧИК ЛИДОВ ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text
    username = update.message.from_user.username or "Клиент"
    
    # 1. Логика ИИ (Интервьюер)
    ai = genai.Client(api_key=GEMINI_KEY)
    response = ai.models.generate_content(
        model="gemini-2.5-flash",
        contents=text,
        config=types.GenerateContentConfig(system_instruction=(
            "Ты — брокер MiK. Собери ЖК, площадь и этаж. "
            "НЕ переводи лид, пока не получишь данные. "
            "Будь вежлив, отвечай кратко."
        ))
    )
    
    reply = response.text
    await update.message.reply_text(reply)
    
    # 2. Если лид полон — отправляем уведомление и в CRM
    if "передаю" in reply.lower() or "свяжется" in reply.lower():
        safe_row = [time.ctime(), username, text]
        save_to_google_async(safe_row)
        await context.bot.send_message(ADMIN_ID, f"🔥 **Полная заявка от @{username}:**\n{text}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
