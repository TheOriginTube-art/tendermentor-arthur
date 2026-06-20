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

def get_tender_advice(profile):
    budget = profile.get("budget", "неизвестно")

    return f"""
🎯 РЕКОМЕНДОВАННЫЕ НАПРАВЛЕНИЯ

На основе твоего профиля:

👉 Уборка помещений
👉 Мелкий ремонт
👉 Благоустройство

💡 Почему:
- низкий порог входа
- можно без опыта
- небольшие контракты

📌 Следующий шаг:
Найти тендер до 10 000–20 000€
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
        user_profile[user_id] = {}
        await update.message.reply_text("В какой стране ты планируешь работать?")
        return

    state = user_state.get(user_id)

    if state == "q1":
        user_profile.setdefault(user_id, {})["country"] = update.message.text
        user_state[user_id] = "q2"
        await update.message.reply_text("💰 Какой у тебя стартовый бюджет? (например: 500€, 1000€)")
        return

    if state == "q2":
        user_profile.setdefault(user_id, {})["budget"] = update.message.text
        user_state[user_id] = "q3"
        await update.message.reply_text("🏢 Есть ли у тебя компания? (ИП, ООО или ещё нет)")
        return

    if state == "q3":
        user_profile.setdefault(user_id, {})["company"] = update.message.text
        user_state[user_id] = "q4"
        await update.message.reply_text("🧠 Есть ли у тебя опыт в тендерах? (да / нет / немного)")
        return

    if state == "q4":
        user_profile.setdefault(user_id, {})["experience"] = update.message.text
        user_state[user_id] = None
        await update.message.reply_text(
            "✅ Профиль заполнен!\n\n" + format_profile(user_id),
            reply_markup=main_menu()
        )
        return

    if state == "analyze":
        prompt = f"""
Ты эксперт по тендерам.

Проанализируй текст:

{text}

Дай:
1. Простое объяснение
2. Требования
3. Риски
4. Стоит ли новичку участвовать
"""
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ]
            )
            await update.message.reply_text(response.choices[0].message.content, reply_markup=main_menu())
        except RateLimitError:
            await update.message.reply_text(
                "⚠️ Превышен лимит запросов к OpenAI. Пожалуйста, пополните баланс на platform.openai.com.",
                reply_markup=main_menu()
            )
        except Exception:
            await update.message.reply_text(
                "⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.",
                reply_markup=main_menu()
            )
        user_state[user_id] = None
        return

    if text == "📊 мой профиль":
        await update.message.reply_text(format_profile(user_id), reply_markup=main_menu())
        return

    if text == "🎯 найти тендер":
        profile = user_profile.get(user_id, {})
        await update.message.reply_text(get_tender_advice(profile), reply_markup=main_menu())
        return

    if text == "📄 анализ тендера":
        user_state[user_id] = "analyze"
        await update.message.reply_text("Пришли текст тендера или описание 📄")
        return

    if text == "💬 спросить ии":
        user_state[user_id] = "chat"
        await update.message.reply_text("Задай вопрос 👇")
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
