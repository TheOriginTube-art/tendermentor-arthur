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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я TenderStart AI\n\n"
        "Я помогу тебе начать тендерный бизнес с нуля.\n\n"
        "Выбери действие в меню 👇",
        reply_markup=main_menu()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_text == "🚀 Начать":
        user_histories[user_id] = []
        await update.message.reply_text(
            "Отлично! Начнём с нуля.\n\nРасскажи немного о себе: есть ли у тебя уже ИП или ООО?",
            reply_markup=main_menu()
        )
        return

    if user_text == "📊 Мой профиль":
        await update.message.reply_text(
            "Раздел профиля в разработке. Скоро здесь появится информация о твоих тендерах и прогрессе.",
            reply_markup=main_menu()
        )
        return

    if user_text == "🎯 Найти тендер":
        await update.message.reply_text(
            "Опиши, в какой сфере хочешь найти тендер — и я помогу разобраться с поиском.",
            reply_markup=main_menu()
        )
        return

    if user_text == "📄 Анализ тендера":
        await update.message.reply_text(
            "Вставь ссылку на тендер или скопируй его описание — разберём вместе.",
            reply_markup=main_menu()
        )
        return

    if user_text == "💬 Спросить ИИ":
        await update.message.reply_text(
            "Задай любой вопрос по тендерному бизнесу — отвечу.",
            reply_markup=main_menu()
        )
        return

    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({"role": "user", "content": user_text})
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
