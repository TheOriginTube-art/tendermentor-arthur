import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты — ИИ-наставник по тендерному бизнесу.
Твоя задача: помогать новичкам без опыта создать бизнес с нуля и довести до первого контракта.

Говори просто.
Давай пошаговые инструкции.
Не перегружай.
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я Tender AI.\n\nПомогу тебе начать тендерный бизнес с нуля.\n\nНапиши: начать"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ]
    )

    answer = response.choices[0].message.content
    await update.message.reply_text(answer)

app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.run_polling()
