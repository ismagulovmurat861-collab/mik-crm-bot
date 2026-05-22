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
import google.generativeai as genai          # ← Исправленный импорт
import requests
from bs4 import BeautifulSoup

# ===================== НАСТРОЙКИ =====================
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

# ===================== FASTAPI =====================
api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK CRM Bot v2.3 — Работает"}

# ===================== GEMINI =====================
genai.configure(api_key=GEMINI_KEY)

# ===================== GOOGLE SHEETS =====================
def get_sheet(sheet_name="baza"):
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
        log.info(f"✅ Подключено к вкладке: {sheet_name}")
        return sheet
    except Exception as e:
        log.error(f"❌ Ошибка Google Sheets: {e}")
        return None

# ===================== ИИ =====================
async def generate_content(title: str, price: str):
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(
            f"Объект: {title}. Цена: {price}. Создай продающий контент для Reels."
        )
        # Простая обработка
        text = response.text
        hook = text.split('\n')[0] if '\n' in text else "🔥 Горячее предложение!"
        sub = text.split('\n')[-1] if '\n' in text else f"{title} — {price}"
        return {"hook": hook, "sub": sub}
    except Exception as e:
        log.error(f"Gemini ошибка: {e}")
        return {"hook": "🔥 Отличный вариант от хозяина!", "sub": f"{title} — {price}"}

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

        for card in cards[:15]:
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

                log.info(f"✅ Найден новый объект: {title[:60]}...")
                return {
                    "id": obj_id,
                    "title": title,
                    "price": price,
                    "url": full_url
                }
            except:
                continue
    except Exception as e:
        log.error(f"Ошибка парсера: {e}")
    return None

# ===================== ОСНОВНАЯ ЗАДАЧА =====================
async def auto_production_job(bot):
    log.info("=== Запуск auto_production_job ===")
    
    today = dt.date.today().strftime("%Y-%m-%d")
    if today not in DAILY_LIMIT_CACHE:
        DAILY_LIMIT_CACHE[today] = 0

    if DAILY_LIMIT_CACHE[today] >= DAILY_LIMIT:
        log.info("Дневной лимит достигнут")
        return

    obj = await parse_krisha()
    if not obj:
        log.info("Новых объектов не найдено")
        return

    PARSED_CACHE.add(obj["id"])
    content = await generate_content(obj["title"], obj["price"])

    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")

    # Запись в baza
    sheet_baza = get_sheet("baza")
    if sheet_baza:
        try:
            row = [
                now_str, "Krisha.kz", obj["title"], obj["price"], "₸", 
                obj["url"], "Астана", "", "", "", "", "", content['hook'], "Новый"
            ]
            sheet_baza.append_row(row)
            log.info("✅ Успешно записано в baza")
        except Exception as e:
            log.error(f"Ошибка записи в baza: {e}")

    DAILY_LIMIT_CACHE[today] += 1
    log.info(f"Обработано объектов сегодня: {DAILY_LIMIT_CACHE[today]}/{DAILY_LIMIT}")

# ===================== ЛИДЫ =====================
async def handle_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    username = update.message.from_user.username or update.message.from_user.first_name
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")

    sheet_leads = get_sheet("leads")
    if sheet_leads:
        row = [now_str, f"@{username}", "", "Telegram", text, "", "", "", "", "Новый"]
        sheet_leads.append_row(row)

    await update.message.reply_text("Спасибо! Записал ваш запрос.")
    await context.bot.send_message(ADMIN_ID, f"Новый лид от @{username}\n{text}")

# ===================== ЗАПУСК =====================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=25, args=[app.bot])
    scheduler.start()

    log.info("🤖 MiK CRM Bot v2.3 успешно запущен")

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Здравствуйте! Я ИИ-помощник MiK Real Estate.")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lead))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
