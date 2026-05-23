import os
import logging
import threading
import time
import json
from dataclasses import dataclass, field
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN         = os.getenv("BOT_TOKEN")
ADMIN_ID          = int(os.getenv("ADMIN_CHAT_ID", "0"))
SPREADSHEET_ID    = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_KEY        = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_KEY)


# ─────────────────────────────────────────────
# СТРУКТУРА ЛИДА
# ─────────────────────────────────────────────
@dataclass
class Lead:
    zhk:        Optional[str] = None   # жилой комплекс
    area:       Optional[str] = None   # площадь
    floor:      Optional[str] = None   # этаж
    phone:      Optional[str] = None   # телефон
    media_count: int          = 0      # кол-во фото/видео

    @property
    def is_complete(self) -> bool:
        return all([self.zhk, self.area, self.floor])

    @property
    def status_text(self) -> str:
        return (
            f"  - ЖК:      {self.zhk   or 'НЕ УКАЗАН'}\n"
            f"  - Площадь: {self.area  or 'НЕ УКАЗАНА'}\n"
            f"  - Этаж:    {self.floor or 'НЕ УКАЗАН'}\n"
            f"  - Телефон: {self.phone or 'НЕ УКАЗАН'}"
        )


@dataclass
class Session:
    lead:       Lead  = field(default_factory=Lead)
    history:    list  = field(default_factory=list)
    username:   str   = ""
    user_id:    int   = 0
    started_at: str   = ""
    done:       bool  = False


sessions: dict[int, Session] = {}


# ─────────────────────────────────────────────
# ПРОМПТЫ
# ─────────────────────────────────────────────
EXTRACT_PROMPT = (
    "Ты парсер данных. Из сообщения клиента извлеки:\n"
    "- zhk  : название жилого комплекса (строка или null)\n"
    "- area : площадь квартиры в м² (строка или null)\n"
    "- floor: этаж (строка или null)\n\n"
    "Верни ТОЛЬКО валидный JSON без markdown:\n"
    "{\"zhk\": ..., \"area\": ..., \"floor\": ...}\n\n"
    "Правила: если данных нет — null. Не придумывай."
)

DIALOG_PROMPT = (
    "Ты брокер-консультант MiK по недвижимости.\n"
    "Твоя задача — собрать четыре параметра: ЖК, площадь, этаж, телефон.\n\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "1. Задавай РОВНО ОДИН вопрос за сообщение\n"
    "2. Спрашивай только то, чего нет в [СТАТУС ДАННЫХ]\n"
    "3. Не повторяй вопросы о данных, которые уже получены\n"
    "4. Телефон можно попросить отправить кнопкой Контакт или написать вручную\n"
    "5. Когда ЖК, площадь и этаж получены — скажи ИМЕННО:\n"
    "   Отлично! Ваша заявка принята. Менеджер свяжется с вами.\n"
    "6. Пока хоть одно из трёх полей пустое — НЕ говори эту фразу"
)

SHEET_HEADERS = ["Дата", "Username", "User ID", "Телефон", "ЖК", "Площадь", "Этаж", "Медиа"]


# ─────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────
def get_or_create_session(user_id: int, username: str) -> Session:
    if user_id not in sessions:
        sessions[user_id] = Session(
            username   = username,
            user_id    = user_id,
            started_at = time.strftime("%Y-%m-%d %H:%M"),
        )
    return sessions[user_id]


def extract_fields(text: str, lead: Lead) -> Lead:
    """Gemini #1: парсит текст и обновляет только пустые поля."""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=EXTRACT_PROMPT)
        resp  = model.generate_content(text)
        raw   = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data  = json.loads(raw)

        def valid(v):
            return v and str(v).strip() not in ("null", "", "None")

        if not lead.zhk   and valid(data.get("zhk")):
            lead.zhk   = str(data["zhk"]).strip()
        if not lead.area  and valid(data.get("area")):
            lead.area  = str(data["area"]).strip()
        if not lead.floor and valid(data.get("floor")):
            lead.floor = str(data["floor"]).strip()
    except Exception as e:
        log.warning(f"extract_fields error: {e}")
    return lead


async def generate_reply(session: Session, user_text: str) -> str:
    """Gemini #2: ведёт диалог зная текущий статус полей."""
    ctx = f"[СТАТУС ДАННЫХ]\n{session.lead.status_text}\n\n[СООБЩЕНИЕ КЛИЕНТА]\n{user_text}"
    session.history.append({"role": "user", "parts": [ctx]})
    try:
        model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=DIALOG_PROMPT)
        resp  = model.generate_content(session.history)
        reply = resp.text.strip()
    except Exception as e:
        log.error(f"Gemini dialog error: {e}")
        reply = "Извините, произошла ошибка. Попробуйте ещё раз."
    session.history.append({"role": "model", "parts": [reply]})
    return reply


async def finalize_lead(session: Session, context) -> None:
    """Сохраняем в CRM и уведомляем админа."""
    session.done = True
    save_to_crm(session)

    if ADMIN_ID:
        media_note = f"\n📎 Медиафайлов: {session.lead.media_count}" if session.lead.media_count else ""
        msg = (
            f"*Новая заявка!*\n\n"
            f"Клиент: @{session.username}\n"
            f"Телефон: {session.lead.phone or 'не указан'}\n"
            f"ЖК: {session.lead.zhk}\n"
            f"Площадь: {session.lead.area}\n"
            f"Этаж: {session.lead.floor}\n"
            f"Время: {session.started_at}{media_note}"
        )
        await context.bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")


def save_to_crm(session: Session):
    def task():
        if not GOOGLE_CREDS_JSON:
            log.warning("GOOGLE_CREDS_JSON не задан — CRM пропущена")
            return
        try:
            creds_data = json.loads(GOOGLE_CREDS_JSON.strip())
            scope  = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds  = ServiceAccountCredentials.from_json_keyfile_dict(creds_data, scope)
            client = gspread.authorize(creds)
            ws     = client.open_by_key(SPREADSHEET_ID).worksheet("clients")

            existing = ws.get_all_values()
            if not existing or existing[0] != SHEET_HEADERS:
                ws.insert_row(SHEET_HEADERS, index=1)

            row = [
                session.started_at,
                f"@{session.username}",
                str(session.user_id),
                session.lead.phone      or "-",
                session.lead.zhk        or "-",
                session.lead.area       or "-",
                session.lead.floor      or "-",
                str(session.lead.media_count) if session.lead.media_count else "0",
            ]
            ws.append_row(row)
            log.info(f"CRM OK: @{session.username} | {row[3:]}")
        except Exception as e:
            log.error(f"CRM Error: {e}")

    threading.Thread(target=task, daemon=True).start()


# ─────────────────────────────────────────────
# ОБРАБОТЧИКИ TELEGRAM
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.message.from_user.id
    uname = update.message.from_user.username or f"id{uid}"
    sessions[uid] = Session(username=uname, user_id=uid, started_at=time.strftime("%Y-%m-%d %H:%M"))
    await update.message.reply_text(
        "Здравствуйте! Я помогу подобрать квартиру. Какой ЖК вас интересует?"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений."""
    if not update.message or not update.message.text:
        return

    text  = update.message.text
    uid   = update.message.from_user.id
    uname = update.message.from_user.username or f"id{uid}"
    session = get_or_create_session(uid, uname)

    if session.done:
        await update.message.reply_text("Ваша заявка уже передана. Менеджер скоро свяжется!")
        return

    # Извлекаем поля + проверяем телефон в тексте
    session.lead = extract_fields(text, session.lead)
    if not session.lead.phone:
        import re
        phone_match = re.search(r"[+7|8]?[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}", text)
        if phone_match:
            session.lead.phone = phone_match.group().strip()

    log.info(f"@{uname} text => ЖК={session.lead.zhk} площадь={session.lead.area} этаж={session.lead.floor} тел={session.lead.phone}")

    reply = await generate_reply(session, text)
    await update.message.reply_text(reply)

    if session.lead.is_complete and not session.done:
        await finalize_lead(session, context)


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка отправленного контакта (кнопка Поделиться контактом)."""
    if not update.message or not update.message.contact:
        return

    uid     = update.message.from_user.id
    uname   = update.message.from_user.username or f"id{uid}"
    contact = update.message.contact
    session = get_or_create_session(uid, uname)

    if session.done:
        await update.message.reply_text("Ваша заявка уже передана. Менеджер скоро свяжется!")
        return

    # Сохраняем телефон
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    session.lead.phone = phone

    name = " ".join(filter(None, [contact.first_name, contact.last_name]))
    log.info(f"@{uname} => контакт: {name} {phone}")

    # Продолжаем диалог
    reply = await generate_reply(session, f"Мой телефон: {phone}, имя: {name}")
    await update.message.reply_text(reply)

    if session.lead.is_complete and not session.done:
        await finalize_lead(session, context)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фотографий — пересылаем админу, фиксируем в сессии."""
    if not update.message:
        return

    uid   = update.message.from_user.id
    uname = update.message.from_user.username or f"id{uid}"
    session = get_or_create_session(uid, uname)

    if session.done:
        await update.message.reply_text("Спасибо за фото! Заявка уже передана.")
        return

    session.lead.media_count += 1
    caption = update.message.caption or ""

    # Пересылаем фото админу
    if ADMIN_ID and update.message.photo:
        file_id = update.message.photo[-1].file_id  # берём максимальное качество
        await context.bot.send_photo(
            ADMIN_ID,
            photo=file_id,
            caption=f"📷 Фото от @{uname}\n{caption}".strip(),
        )

    log.info(f"@{uname} => фото #{session.lead.media_count}")
    await update.message.reply_text("Фото получено! Оно будет передано менеджеру вместе с заявкой.")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка видео — пересылаем админу, фиксируем в сессии."""
    if not update.message:
        return

    uid   = update.message.from_user.id
    uname = update.message.from_user.username or f"id{uid}"
    session = get_or_create_session(uid, uname)

    if session.done:
        await update.message.reply_text("Спасибо за видео! Заявка уже передана.")
        return

    session.lead.media_count += 1
    caption = update.message.caption or ""

    if ADMIN_ID:
        # Видео пересылаем напрямую (forward сохраняет качество)
        await update.message.forward(ADMIN_ID)
        if caption:
            await context.bot.send_message(ADMIN_ID, f"👆 Видео от @{uname}\n{caption}")
        else:
            await context.bot.send_message(ADMIN_ID, f"👆 Видео от @{uname}")

    log.info(f"@{uname} => видео #{session.lead.media_count}")
    await update.message.reply_text("Видео получено! Оно будет передано менеджеру вместе с заявкой.")


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.CONTACT,                   handle_contact))
    app.add_handler(MessageHandler(filters.PHOTO,                     handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,   handle_text))

    log.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
