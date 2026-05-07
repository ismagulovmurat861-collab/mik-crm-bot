from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from config import BOT_TOKEN, OPENAI_API_KEY
from crm import add_lead

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM = "Ты риэлтор. Веди клиента к заявке."

def ask_gpt(text):
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text}
        ]
    )
    return res.choices[0].message.content

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши бюджет — подберу квартиру")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.message.from_user.id

    reply = ask_gpt(text)

    await update.message.reply_text(reply)

    add_lead(user_id, text)

def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, handle))

    app.run_polling()
