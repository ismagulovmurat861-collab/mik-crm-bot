import os, logging, threading, re, json, time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Настройка
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

# --- ВАШИ ДАННЫЕ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# --- ФОНОВАЯ ЗАПИСЬ В CRM (БЕЗОПАСНАЯ) ---
def safe_crm_save(data_row, sheet_name="clients"):
    def save_task():
        try:
            # Автоматическая починка JSON ключа
            creds_data = json.loads(GOOGLE_CREDS_JSON.strip())
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_data, scope)
            client = gspread.authorize(creds)
            client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name).append_row(data_row)
        except Exception as e:
            log.error(f"CRM Error: {e}")
    threading.Thread(target=save_task, daemon=True).start()

# --- ОСНОВНОЙ ЛОГИЧЕСКИЙ ЦИКЛ ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    username = update.message.from_user.username or "Клиент"
    
    # 1. СТРОГИЙ ТРИГГЕР КАЛЬКУЛЯТОРА
    if any(w in text.lower() for w in ["ипотека", "рассчитать", "калькулятор"]):
        nums = [int(s) for s in re.findall(r'\d+', text.replace(" ", ""))]
        if nums and nums[0] > 1000000: # Только если сумма > 1 млн
            await update.message.reply_text("🧮 Принял, считаю ипотеку... Передаю ваш запрос менеджеру.")
            safe_crm_save([time.ctime(), username, text, "Ипотечный запрос"])
            return

    # 2. ИНТЕРВЬЮЕР (Сбор данных)
    ai = genai.Client(api_key=GEMINI_KEY)
    res = ai.models.generate_content(
        model="gemini-2.5-flash",
        contents=text,
        config=types.GenerateContentConfig(system_instruction=(
            "Ты — брокер MiK. Собери ЖК, площадь и этаж. "
            "НЕ пиши 'передаю менеджеру', пока не получишь все данные. "
            "Будь вежлив, пиши кратко."
        ))
    )
    await update.message.reply_text(res.text)
    
    # 3. ПОЛНАЯ ЗАЯВКА (Уведомление тебе)
    if "передаю" in res.text.lower() or "свяжется" in res.text.lower():
        safe_crm_save([time.ctime(), username, text], "clients")
        await context.bot.send_message(ADMIN_ID, f"🔥 **Полная заявка от @{username}:**\n{text}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
