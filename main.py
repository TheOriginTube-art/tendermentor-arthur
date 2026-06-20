import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI, RateLimitError, APIError

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

def main_menu():
    keyboard = [
        ["🚀 Начать"],
        ["📊 Мой профиль"],
        ["🎯 Найти тендер"],
        ["📄 Анализ тендера"],
        ["💬 Спросить ИИ"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

user_histories = {}
user_state = {}
user_profile = {}

def format_profile(user_id):
    profile = user_profile.get(user_id)

    if not profile:
        return "Профиль пуст. Нажми 🚀 Начать"

    return f"""
📊 ТВОЙ ПРОФИЛЬ

🌍 Страна: {profile.get('country', '-')}

💰 Бюджет: {profile.get('budget', '-')}

🏢 Компания: {profile.get('company', '-')}

🧠 Опыт: {profile.get('experience', '-')}

🎯 Статус: Новичок в тендерах
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я TenderStart AI\n\n"
        "Я помогу тебе начать тендерный бизнес с нуля.\n\n"
        "Выбери действие в меню 👇",
        reply_markup=main_menu()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    text = update.message.text.lower()

    if text == "🚀 начать":
        user_state[user_id] = "q1"
        await update.message.reply_text("В какой стране ты планируешь работать?")
        return

    if text == "📊 мой профиль":
        profile = user_profile.get(user_id, {})
        await update.message.reply_text(f"Твой профиль:\n{profile}")
        return

    if text == "🎯 найти тендер":
        await update.message.reply_text("Я скоро буду подбирать тендеры 👍")
        return

    if text == "📄 анализ тендера":
        await update.message.reply_text("Пришли мне текст или файл тендера 📄")
        return

    if text == "💬 спросить ии":
        await update.message.reply_text("Напиши свой вопрос 👇")
        return

    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({"role": "user", "content": text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[user_id]

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )
        answer = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": answer})

        if len(user_histories[user_id]) > 40:
            user_histories[user_id] = user_histories[user_id][-40:]

        await update.message.reply_text(answer, reply_markup=main_menu())

    except RateLimitError:
        user_histories[user_id].pop()
        await update.message.reply_text(
            "⚠️ Превышен лимит запросов к OpenAI. Пожалуйста, пополните баланс на platform.openai.com и попробуйте снова.",
            reply_markup=main_menu()
        )
    except APIError as e:
        user_histories[user_id].pop()
        await update.message.reply_text(
            f"⚠️ Ошибка OpenAI: {str(e)}\n\nПопробуйте ещё раз.",
            reply_markup=main_menu()
        )
    except Exception:
        user_histories[user_id].pop()
        await update.message.reply_text(
            "⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.",
            reply_markup=main_menu()
        )

app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.run_polling()
