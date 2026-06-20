import os
import json
import itertools
from datetime import datetime, timezone, time as dtime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
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

def build_system_prompt(user_id):
    profile = user_profile.get(user_id)
    if not profile:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + f"""
Профиль пользователя:
- Страна: {profile.get('country', '-')}
- Бюджет: {profile.get('budget', '-')}
- Компания: {profile.get('company', '-')}
- Опыт: {profile.get('experience', '-')}

Учитывай эти данные при ответах. Давай советы, подходящие именно этому пользователю.
"""

def main_menu(user_id=None):
    keyboard = []
    if user_id is None or not user_profile.get(user_id):
        keyboard.append(["🚀 Начать"])
    else:
        profile = user_profile.get(user_id, {})
        if profile.get("familiarized"):
            keyboard.append(["🔁 Ознакомиться снова"])
            keyboard += [
                ["📊 Мой профиль"],
                ["🎯 Найти тендер"],
                ["📄 Анализ тендера"],
                ["💬 Спросить ИИ"]
            ]
        else:
            keyboard.append(["📖 Ознакомиться"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

TENDER_INFO = """
📚 ЧТО ТАКОЕ ТЕНДЕР?

Тендер — это конкурс, где заказчик (государство или крупная компания) выбирает лучшего исполнителя для выполнения работы или поставки товара.

Побеждает тот, кто предложил лучшие условия — чаще всего наименьшую цену.

━━━━━━━━━━━━━━━━━━━━━
🔄 ЭТАПЫ УЧАСТИЯ В ТЕНДЕРЕ
━━━━━━━━━━━━━━━━━━━━━

1️⃣ ПОИСК ТЕНДЕРА
   Заходишь на zakupki.gov.ru и ищешь подходящий лот по своей сфере и бюджету.

2️⃣ ИЗУЧЕНИЕ ДОКУМЕНТАЦИИ
   Читаешь техническое задание (ТЗ): что нужно сделать, в какой срок, какие требования к участнику.

3️⃣ РЕГИСТРАЦИЯ НА ПЛОЩАДКЕ
   Регистрируешься на электронной торговой площадке (ЭТП), где проводится тендер. Популярные: Сбербанк-АСТ, РТС-Тендер, Росэлторг.

4️⃣ ПОДГОТОВКА ЗАЯВКИ
   Собираешь пакет документов: выписка из ЕГРИП/ЕГРЮЛ, лицензии (если нужны), ценовое предложение.

5️⃣ ОБЕСПЕЧЕНИЕ ЗАЯВКИ
   Вносишь залог (0.5–5% от суммы контракта) — это подтверждает, что ты серьёзный участник. Деньги возвращают после подведения итогов.

6️⃣ ПОДАЧА ЗАЯВКИ
   Загружаешь документы на площадку до указанного дедлайна.

7️⃣ РАССМОТРЕНИЕ ЗАЯВОК
   Заказчик проверяет все заявки. Кто не соответствует — отстраняется.

8️⃣ ТОРГИ / АУКЦИОН
   В назначенный день участники снижают цену в режиме онлайн. Побеждает тот, кто предложил минимальную цену.

9️⃣ ЗАКЛЮЧЕНИЕ КОНТРАКТА
   Победитель подписывает контракт и вносит обеспечение исполнения (5–30% от суммы).

🔟 ИСПОЛНЕНИЕ И ОПЛАТА
   Выполняешь работу или поставляешь товар. После приёмки заказчик оплачивает контракт.

━━━━━━━━━━━━━━━━━━━━━
💡 СОВЕТ НОВИЧКУ

Начни с небольших тендеров до 500 000₽ — там меньше конкурентов и проще документация. Первые 2–3 контракта дадут тебе опыт и репутацию.
"""

PROFILES_FILE = "profiles.json"

def load_profiles():
    if os.path.exists(PROFILES_FILE):
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    return {}

def save_profiles():
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in user_profile.items()}, f, ensure_ascii=False, indent=2)

user_histories = {}
user_state = {}
user_profile = load_profiles()

def get_status(count):
    if count >= 20:
        return "🏆 Опытный участник"
    elif count >= 5:
        return "📈 Участник"
    return "🌱 Новичок"

def format_duration(registered_at_str):
    try:
        registered_at = datetime.fromisoformat(registered_at_str)
        now = datetime.now(timezone.utc)
        delta = now - registered_at
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        if days > 0:
            return f"{days} дн. {hours} ч."
        elif hours > 0:
            return f"{hours} ч. {minutes} мин."
        else:
            return f"{minutes} мин."
    except Exception:
        return "-"

def format_profile(user_id):
    profile = user_profile.get(user_id)

    if not profile:
        return "Профиль пуст. Нажми 🚀 Начать"

    duration = format_duration(profile.get("registered_at", ""))
    count = profile.get("analyzed_count", 0)

    status = get_status(count)

    return f"""
📊 ТВОЙ ПРОФИЛЬ

🌍 Страна: {profile.get('country', '-')}

💰 Бюджет: {profile.get('budget', '-')}

🏢 Компания: {profile.get('company_name') or profile.get('company', '-')}

🧠 Опыт: {profile.get('experience', '-')}

📋 Проанализировано тендеров: {count}

⏱ Время твоего опыта: {duration}

🎯 Статус: {status}
"""

def get_tender_advice(profile):
    budget = profile.get("budget", "")
    company = profile.get("company", "")
    experience = profile.get("experience", "")

    if "500 000" in budget or "500000" in budget:
        budget_tier = "high"
    elif "300 000" in budget or "300000" in budget:
        budget_tier = "medium"
    else:
        budget_tier = "low"

    has_company = "ип" in company.lower() or "ооо" in company.lower()
    has_experience = "есть опыт" in experience.lower() or "✅" in experience

    if budget_tier == "high" and has_company:
        directions = ["🏗 Строительные работы", "🚛 Поставка оборудования", "🧹 Клининг крупных объектов"]
        limit = "1 000 000–5 000 000₽"
    elif budget_tier == "medium" and has_company:
        directions = ["🔧 Техническое обслуживание", "📦 Поставка товаров", "🌿 Благоустройство"]
        limit = "300 000–1 000 000₽"
    elif not has_company:
        directions = ["🧹 Уборка помещений", "🌱 Мелкое благоустройство", "📋 Консультационные услуги"]
        limit = "50 000–300 000₽"
    else:
        directions = ["🧹 Уборка помещений", "🔨 Мелкий ремонт", "🌿 Благоустройство"]
        limit = "100 000–500 000₽"

    why = "без опыта" if not has_experience else "соответствует твоему опыту"

    return f"""
🎯 РЕКОМЕНДОВАННЫЕ НАПРАВЛЕНИЯ

На основе твоего профиля:

{"  ".join(f"{chr(10)}👉 {d}" for d in directions)}

💡 Почему:
- низкий порог входа
- {why}
- подходящий размер контрактов

📌 Следующий шаг:
Найти тендер до {limit}
"""

def profile_inline_kb(user_id):
    has_company = bool(user_profile.get(user_id, {}).get("company_name"))
    company_label = "✏️ Редактировать компанию" if has_company else "➕ Добавить компанию"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(company_label, callback_data="profile_edit_company")],
        [InlineKeyboardButton("⬅️ Вернуться в меню", callback_data="profile_back")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    await update.message.reply_text(
        "👋 Привет! Я TenderStart AI\n\n"
        "Я помогу тебе начать тендерный бизнес с нуля.\n\n"
        "Выбери действие в меню 👇",
        reply_markup=main_menu(user_id)
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    user_profile.pop(user_id, None)
    user_state.pop(user_id, None)
    user_histories.pop(user_id, None)
    save_profiles()
    await update.message.reply_text(
        "🔄 Профиль сброшен. Нажми 🚀 Начать, чтобы заполнить заново.",
        reply_markup=main_menu(user_id)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    text = update.message.text.lower()

    if text == "🚀 начать":
        user_state[user_id] = "q1"
        user_profile[user_id] = {
            "registered_at": datetime.now(timezone.utc).isoformat()
        }
        country_keyboard = ReplyKeyboardMarkup([["🇷🇺 Россия"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("В какой стране ты планируешь работать?", reply_markup=country_keyboard)
        return

    state = user_state.get(user_id)

    if state == "q1":
        user_profile.setdefault(user_id, {})["country"] = update.message.text
        user_state[user_id] = "q2"
        budget_keyboard = ReplyKeyboardMarkup(
            [["50 000₽", "100 000₽"], ["300 000₽", "500 000₽+"]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await update.message.reply_text("💰 Какой у тебя стартовый бюджет?", reply_markup=budget_keyboard)
        return

    if state == "q2":
        user_profile.setdefault(user_id, {})["budget"] = update.message.text
        user_state[user_id] = "q3"
        company_keyboard = ReplyKeyboardMarkup(
            [["🏢 ИП", "🏦 ООО", "❌ Ещё нет"]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await update.message.reply_text("🏢 Есть ли у тебя компания?", reply_markup=company_keyboard)
        return

    if state == "q3":
        user_profile.setdefault(user_id, {})["company"] = update.message.text
        user_state[user_id] = "q4"
        experience_keyboard = ReplyKeyboardMarkup(
            [["❌ Нет опыта", "🌱 Немного", "✅ Есть опыт"]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await update.message.reply_text("🧠 Есть ли у тебя опыт в тендерах?", reply_markup=experience_keyboard)
        return

    if state == "q4":
        user_profile.setdefault(user_id, {})["experience"] = update.message.text
        user_state[user_id] = None
        save_profiles()
        await update.message.reply_text(
            "✅ Профиль заполнен!\n\n" + format_profile(user_id),
            reply_markup=main_menu(user_id)
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
                    {"role": "system", "content": build_system_prompt(user_id)},
                    {"role": "user", "content": prompt}
                ]
            )
            p = user_profile.setdefault(user_id, {})
            old_status = get_status(p.get("analyzed_count", 0))
            p["analyzed_count"] = p.get("analyzed_count", 0) + 1
            new_status = get_status(p["analyzed_count"])
            save_profiles()
            await update.message.reply_text(response.choices[0].message.content, reply_markup=main_menu(user_id))
            if new_status != old_status:
                await update.message.reply_text(
                    f"🎉 Поздравляю! Ты получил новый статус: {new_status}",
                    reply_markup=main_menu(user_id)
                )
        except RateLimitError:
            await update.message.reply_text(
                "⚠️ Превышен лимит запросов к OpenAI. Пожалуйста, пополните баланс на platform.openai.com.",
                reply_markup=main_menu(user_id)
            )
        except Exception:
            await update.message.reply_text(
                "⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.",
                reply_markup=main_menu(user_id)
            )
        user_state[user_id] = None
        return

    if text in ("📖 ознакомиться", "🔁 ознакомиться снова"):
        confirm_kb = ReplyKeyboardMarkup([["✅ Ознакомлен"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(TENDER_INFO, reply_markup=confirm_kb)
        return

    if text == "✅ ознакомлен":
        user_profile.setdefault(user_id, {})["familiarized"] = True
        save_profiles()
        await update.message.reply_text(
            "🎓 Отлично! Теперь ты знаешь основы тендерного бизнеса.\n\nВыбери следующий шаг 👇",
            reply_markup=main_menu(user_id)
        )
        return

    if text == "📊 мой профиль":
        with open("profile_banner.png", "rb") as photo:
            await update.message.reply_photo(photo, reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text(format_profile(user_id), reply_markup=profile_inline_kb(user_id))
        return

    if state == "add_company":
        user_profile.setdefault(user_id, {})["company_name"] = update.message.text
        user_state[user_id] = None
        save_profiles()
        with open("profile_banner.png", "rb") as photo:
            await update.message.reply_photo(photo)
        await update.message.reply_text(
            f"✅ Компания сохранена: {update.message.text}\n\n" + format_profile(user_id),
            reply_markup=profile_inline_kb(user_id)
        )
        return

    if text == "🎯 найти тендер":
        profile = user_profile.get(user_id, {})
        await update.message.reply_text(get_tender_advice(profile), reply_markup=main_menu(user_id))
        return

    if text == "📄 анализ тендера":
        user_state[user_id] = "analyze"
        await update.message.reply_text("Пришли текст тендера или описание 📄")
        return

    if state == "chat":
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": build_system_prompt(user_id)},
                    {"role": "user", "content": text}
                ]
            )
            await update.message.reply_text(response.choices[0].message.content, reply_markup=main_menu(user_id))
        except RateLimitError:
            await update.message.reply_text(
                "⚠️ Превышен лимит запросов к OpenAI. Пожалуйста, пополните баланс на platform.openai.com.",
                reply_markup=main_menu(user_id)
            )
        except Exception:
            await update.message.reply_text(
                "⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.",
                reply_markup=main_menu(user_id)
            )
        user_state[user_id] = None
        return

    if text == "💬 спросить ии":
        user_state[user_id] = "chat"
        await update.message.reply_text("Задай вопрос 👇")
        return

    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({"role": "user", "content": text})
    messages = [{"role": "system", "content": build_system_prompt(user_id)}] + user_histories[user_id]

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )
        answer = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": answer})

        if len(user_histories[user_id]) > 40:
            user_histories[user_id] = user_histories[user_id][-40:]

        await update.message.reply_text(answer, reply_markup=main_menu(user_id))

    except RateLimitError:
        user_histories[user_id].pop()
        await update.message.reply_text(
            "⚠️ Превышен лимит запросов к OpenAI. Пожалуйста, пополните баланс на platform.openai.com и попробуйте снова.",
            reply_markup=main_menu(user_id)
        )
    except APIError as e:
        user_histories[user_id].pop()
        await update.message.reply_text(
            f"⚠️ Ошибка OpenAI: {str(e)}\n\nПопробуйте ещё раз.",
            reply_markup=main_menu(user_id)
        )
    except Exception:
        user_histories[user_id].pop()
        await update.message.reply_text(
            "⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.",
            reply_markup=main_menu(user_id)
        )

DAILY_TIPS = [
    "💡 Совет дня: Начни с тендеров до 500 000₽ — меньше конкурентов и проще документация.",
    "💡 Совет дня: Зарегистрируйся на портале zakupki.gov.ru — там публикуются все госзакупки России.",
    "💡 Совет дня: Читай требования к участнику внимательно — часто отказывают из-за мелких ошибок в документах.",
    "💡 Совет дня: ИП может участвовать в тендерах наравне с ООО — не жди открытия компании.",
    "💡 Совет дня: Первый тендер лучше выбирать в знакомой сфере — это повышает шансы на победу.",
    "💡 Совет дня: Обеспечение заявки — это залог серьёзности. Обычно 0.5–5% от суммы контракта.",
    "💡 Совет дня: Сохраняй все документы по выигранным тендерам — они пригодятся для будущих заявок.",
    "💡 Совет дня: Используй 44-ФЗ для госзакупок и 223-ФЗ для закупок компаний с госучастием.",
]

tip_cycle = itertools.cycle(DAILY_TIPS)

async def send_daily_tip(context):
    tip = next(tip_cycle)
    for user_id in list(user_profile.keys()):
        try:
            await context.bot.send_message(chat_id=user_id, text=tip)
        except Exception:
            pass

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat_id
    data = query.data

    if data == "profile_back":
        await query.message.reply_text("Главное меню 👇", reply_markup=main_menu(user_id))

    elif data == "profile_edit_company":
        user_state[user_id] = "add_company"
        await query.message.reply_text(
            "🏢 Введи название своей компании (например: ООО «Ромашка» или ИП Иванов И.И.):"
        )

app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(CallbackQueryHandler(callback_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.job_queue.run_daily(send_daily_tip, time=dtime(hour=6, minute=0, tzinfo=timezone.utc))

app.run_polling()
