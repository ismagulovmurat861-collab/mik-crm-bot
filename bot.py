import os
import logging
import asyncio
import datetime as dt
from fastapi import FastAPI
from google import genai
from google.genai import types
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from PIL import Image as PILImage, ImageDraw, ImageFont

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# Чтение переменных окружения из Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# Глобальные кэши (чтобы не было каши в таблице и лимит строго работал)
DAILY_LIMIT_CACHE = {}
PARSED_CACHE = set()

# Промпты для ИИ
AGENT_SYSTEM = "Ты — ИИ-брокер компании MiK Real Estate в Астане. Твоя цель — вежливо и профессионально помогать клиентам."
CONTENT_FACTORY_SYSTEM = """Ты — креативный продюсер Reels/TikTok. Твоя задача — выдать мощный заголовок и субтитры.
Ответь СТРОГО в формате JSON без лишнего текста и без кавычек markdown:
{"hook": "ЖЕСТКИЙ_ХУК", "sub": "ГЛАВНОЕ_ПРЕИМУЩЕСТВО"}"""

# Создаем веб-сервер, чтобы Render не усыплял бота
api_app = FastAPI()

@api_app.get("/")
def read_root():
    return {"status": "MiK Production Factory is running online"}

# СБОРКА ВИДЕО И НАЛОЖЕНИЕ ТЕКСТА
def create_auto_video(image_paths, hook_text, sub_text, output_path="result.mp4"):
    try:
        processed_images = []
        for i, img_path in enumerate(image_paths):
            with PILImage.open(img_path) as img:
                img.thumbnail((1080, 1920))
                background = PILImage.new('RGB', (1080, 1920), (0, 0, 0))
                offset = ((1080 - img.width) // 2, (1920 - img.height) // 2)
                background.paste(img, offset)
                
                # Текст накладываем только на первые два кадра для удержания внимания
                if i < 2:
                    draw = ImageDraw.Draw(background)
                    try:
                        font_hook = ImageFont.load_default(size=60)
                        font_sub = ImageFont.load_default(size=40)
                    except:
                        font_hook = ImageFont.load_default()
                        font_sub = ImageFont.load_default()
                        
                    draw.text((540, 800), hook_text, fill="yellow", font=font_hook, anchor="mm")
                    draw.text((540, 950), sub_text, fill="white", font=font_sub, anchor="mm")
                
                frame_path = f"frame_{i}.jpg"
                background.save(frame_path, "JPEG")
                processed_images.append(frame_path)
        
        # Симулируем генерацию готового видеофайла
        if processed_images:
            os.rename(processed_images[0], output_path)
            return output_path
    except Exception as e:
        log.error(f"Ошибка при сборке видео: {e}")
    return None

# ПОДКЛЮЧЕНИЕ GOOGLE ТАБЛИЦЫ
async def save_object_to_sheet(date_str, source, title, price, phone):
    try:
        import json
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("baza")
        sheet.append_row([date_str, source, title, price, phone])
        log.info("Данные успешно добавлены в Google Таблицу!")
    except Exception as e:
        log.error(f"Ошибка Google Таблиц: {e}")

# ИИ ГЕНЕРАЦИЯ СЦЕНАРИЕВ ЧЕРЕЗ GEMINI
async def get_ai_titles(title, price):
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Объект: {title}, Цена: {price}. Придумай хук для Reels.",
            config=types.GenerateContentConfig(
                system_instruction=CONTENT_FACTORY_SYSTEM,
                response_mime_type="application/json"
            )
        )
        import json
        return json.loads(response.text.strip())
    except Exception as e:
        log.error(f"Ошибка Gemini ИИ: {e}")
        return {"hook": "Горячая продажа!", "sub": f"{title} в Астане"}

# ПАРСЕР И АВТОМАТИЧЕСКИЙ КОНТЕНТ-ЗАВОД
async def auto_production_job(bot):
    log.info("🔍 Парсер запущен. Проверяем площадки...")
    
    today_date = dt.date.today().strftime("%Y-%m-%d")
    
    # Инициализируем день в кэше
    if today_date not in DAILY_LIMIT_CACHE:
        DAILY_LIMIT_CACHE[today_date] = 0
        
    # ЖЕСТКИЙ ЛИМИТ: Проверяем, не набралось ли уже 8 объектов за сегодня
    if DAILY_LIMIT_CACHE[today_date] >= 8:
        log.info(f"🚫 Лимит в 8 объектов на сегодня ({today_date}) исчерпан. Пропускаем сбор данных.")
        return

    # Симуляция парсинга уникального ID объявления
    demo_id = f"parsed_{dt.datetime.now().strftime('%H%M%S')}"
    if demo_id in PARSED_CACHE: 
        return
    PARSED_CACHE.add(demo_id)
    
    title = "2-комнатная квартира, ЖК Времена года"
    price = "29 000 000 ₸"
    phone = "+7702888XX"
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Создаем временные картинки для видео-ролика
    local_images = []
    for color, name in [((180,40,40), "t1.jpg"), ((40,180,40), "t2.jpg")]:
        img = PILImage.new('RGB', (800, 600), color)
        img.save(name)
        local_images.append(name)
        
    # Запрашиваем у Gemini ИИ цепляющие заголовки
    text_data = await get_ai_titles(title, price)
    
    # Сохраняем объект в первую вкладку таблицы "baza"
    await save_object_to_sheet(now_str, "Парсер (Крыша)", title, price, phone)
    
    # Собираем Reels видео
    video_file = create_auto_video(local_images, text_data['hook'], text_data['sub'])
    
    # Отправляем отчет и видео риелтору в личку
    caption = f"🤖 **Контент-Завод MiK Real Estate**\n\n📍 Найдена квартира от хозяина!\n🏠 {title}\n💰 Цена: {price}\n📞 Тел: {phone}\n\n🔥 ИИ сгенерировал хук:\n`{text_data['hook']}`"
    
    try:
        if video_file and os.path.exists(video_file):
            with open(video_file, 'rb') as video:
                await bot.send_video(chat_id=ADMIN_ID, video=video, caption=caption, parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=ADMIN_ID, text=caption, parse_mode="Markdown")
    except Exception as e:
        log.error(f"Не удалось отправить видео в телеграм: {e}")

    # Увеличиваем счетчик обработанных объектов за день
    DAILY_LIMIT_CACHE[today_date] += 1
    log.info(f"✅ Объект успешно добавлен. Всего за сегодня: {DAILY_LIMIT_CACHE[today_date]}/8")

# РАБОТА С КЛИЕНТАМИ ЧЕРЕЗ ТЕЛЕГРАМ
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.message.chat_id
    username = update.message.from_user.username or "Покупатель"
    
    log.info(f"Сообщение от пользователя {username}: {user_text}")
    
    # Подключаем Gemini для ответа клиенту в стиле ИИ-брокера
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
            config=types.GenerateContentConfig(system_instruction=AGENT_SYSTEM)
        )
        reply_text = response.text
    except Exception as e:
        log.error(f"Ошибка ИИ при ответе клиенту: {e}")
        reply_text = "Спасибо за обращение! Наш брокер свяжется с вами в ближайшее время."
        
    await update.message.reply_text(reply_text)
    
    # Записываем заявку клиента во вторую вкладку Google Таблицы (если настроена)
    now_str = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    await save_object_to_sheet(now_str, f"Чат-бот (@{username})", user_text, "Клиент", f"ID: {chat_id}")
    
    # Уведомляем тебя в личку о новом клиенте
    await context.bot.send_message(ADMIN_ID, f"🔥 **Новый клиент в системе!**\n👤 Юзер: @{username}\n💬 Текст: {user_text}")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Здравствуйте! Я ваш персональный ИИ-ассистент по недвижимости в Астане. Чем могу помочь?")

# ИНИЦИАЛИЗАЦИЯ И СОВМЕСТНЫЙ ЗАПУСК
async def run_bot_and_web():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Таймер парсера и монтажера (каждые 30 минут)
    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_production_job, "interval", minutes=30, args=[app.bot])
    scheduler.start()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("🤖 Телеграм-модуль успешно запущен.")

def main():
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot_and_web())
    
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(api_app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
