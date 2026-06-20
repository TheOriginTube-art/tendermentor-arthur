import os
import json
import re
import itertools
import asyncio
from datetime import datetime, timezone, time as dtime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from openai import OpenAI, RateLimitError, APIError
import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты — ИИ-наставник по тендерному бизнесу.
Твоя задача: помогать новичкам без опыта создать бизнес с нуля и довести до первого контракта.

Говори просто. Давай пошаговые инструкции. Не перегружай.

ВАЖНО — правила форматирования (Telegram Markdown):
- Заголовки и важные слова: *жирный текст* (одна звёздочка с каждой стороны)
- Курсив для пояснений и примеров: _курсив_ (нижнее подчёркивание)
- Никогда не используй ## ### ** __ — они не работают в Telegram
- Списки делай через цифры или emoji-буллеты (•, ➡️, ✅, 📌)
- Разделяй блоки пустой строкой для читаемости
"""

def fmt_ai(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'__(.+?)__', r'_\1_', text)
    text = re.sub(r'^#{1,3}\s*(.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'`{3}.*?`{3}', '', text, flags=re.DOTALL)
    for ch in ['[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, ch) 
    return text.strip()

async def safe_reply(message, text, **kwargs):
    cleaned = fmt_ai(text)
    try:
        await message.reply_text(cleaned, parse_mode='Markdown', **kwargs)
    except Exception:
        await message.reply_text(cleaned, **kwargs)

async def safe_edit(message, text, **kwargs):
    cleaned = fmt_ai(text)
    try:
        await message.edit_text(cleaned, parse_mode='Markdown', **kwargs)
    except Exception:
        await message.edit_text(cleaned, **kwargs)

PARSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ru-RU,ru;q=0.9',
}

TENDER_TOPICS = {
    "clean":     ("🧹 Уборка и клининг",      "уборка клининг помещений"),
    "garden":    ("🌿 Благоустройство",        "благоустройство озеленение"),
    "repair":    ("🔧 Ремонт и строительство", "ремонт строительство"),
    "supply":    ("📦 Поставка товаров",        "поставка товаров материалов"),
    "it":        ("🖥 IT и оборудование",       "компьютеры оборудование программное обеспечение"),
    "transport": ("🚛 Перевозки и логистика",  "транспортные услуги перевозка"),
    "food":      ("🍽 Питание и продукты",      "питание продукты поставка"),
    "service":   ("📋 Прочие услуги",           "услуги консультации охрана"),
}

def tender_topic_inline_kb(amount):
    fmt = f"{amount:,}".replace(",", " ")
    buttons = []
    keys = list(TENDER_TOPICS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for key in keys[i:i+2]:
            label, _ = TENDER_TOPICS[key]
            row.append(InlineKeyboardButton(label, callback_data=f"tender_topic_{amount}_{key}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ Другая сумма", callback_data="tender_custom_amount")])
    buttons.append([InlineKeyboardButton("⬅️ Изменить сумму", callback_data="tender_back")])
    return InlineKeyboardMarkup(buttons)

def _budget_keyword(amount):
    if amount <= 100000:
        return "уборка помещений благоустройство"
    elif amount <= 200000:
        return "техническое обслуживание поставка"
    elif amount <= 300000:
        return "благоустройство ремонт"
    elif amount <= 400000:
        return "строительные работы монтаж"
    else:
        return "строительство ремонт поставка"

def _parse_amount(text):
    digits = re.sub(r'[^\d]', '', text)
    return int(digits) if digits else 0

# Маппинг: город/регион → путь на zakupki360.ru
CITY_REGION_MAP = {
    "москва": "/region/cfo/moskva",
    "московская область": "/region/cfo/moskovskaya-oblast",
    "санкт-петербург": "/region/szfo/sankt-peterburg",
    "питер": "/region/szfo/sankt-peterburg",
    "спб": "/region/szfo/sankt-peterburg",
    "краснодар": "/region/yufo/krasnodarskij-kraj",
    "краснодарский край": "/region/yufo/krasnodarskij-kraj",
    "сочи": "/region/yufo/krasnodarskij-kraj",
    "новороссийск": "/region/yufo/krasnodarskij-kraj",
    "екатеринбург": "/region/ufo/sverdlovskaya-oblast",
    "свердловская область": "/region/ufo/sverdlovskaya-oblast",
    "нижний тагил": "/region/ufo/sverdlovskaya-oblast",
    "новосибирск": "/region/sfo/novosibirskaya-oblast",
    "новосибирская область": "/region/sfo/novosibirskaya-oblast",
    "казань": "/region/pfo/tatarstan-respublika",
    "татарстан": "/region/pfo/tatarstan-respublika",
    "набережные челны": "/region/pfo/tatarstan-respublika",
    "челябинск": "/region/ufo/chelyabinskaya-oblast",
    "магнитогорск": "/region/ufo/chelyabinskaya-oblast",
    "челябинская область": "/region/ufo/chelyabinskaya-oblast",
    "омск": "/region/sfo/omskaya-oblast",
    "омская область": "/region/sfo/omskaya-oblast",
    "самара": "/region/pfo/samarskaya-oblast",
    "тольятти": "/region/pfo/samarskaya-oblast",
    "самарская область": "/region/pfo/samarskaya-oblast",
    "ростов": "/region/yufo/rostovskaya-oblast",
    "ростов-на-дону": "/region/yufo/rostovskaya-oblast",
    "ростовская область": "/region/yufo/rostovskaya-oblast",
    "уфа": "/region/pfo/bashkortostan-respublika",
    "башкортостан": "/region/pfo/bashkortostan-respublika",
    "пермь": "/region/pfo/permskij-kraj",
    "пермский край": "/region/pfo/permskij-kraj",
    "воронеж": "/region/cfo/voronezhskaya-oblast",
    "волгоград": "/region/yufo/volgogradskaya-oblast",
    "красноярск": "/region/sfo/krasnoyarskij-kraj",
    "красноярский край": "/region/sfo/krasnoyarskij-kraj",
    "саратов": "/region/pfo/saratovskaya-oblast",
    "тюмень": "/region/ufo/tyumenskaya-oblast",
    "тюменская область": "/region/ufo/tyumenskaya-oblast",
    "сургут": "/region/ufo/hmao",
    "иркутск": "/region/sfo/irkutskaya-oblast",
    "иркутская область": "/region/sfo/irkutskaya-oblast",
    "барнаул": "/region/sfo/altajskij-kraj",
    "алтайский край": "/region/sfo/altajskij-kraj",
    "ульяновск": "/region/pfo/ulyanovskaya-oblast",
    "хабаровск": "/region/dfo/habarovskij-kraj",
    "владивосток": "/region/dfo/primorskij-kraj",
    "приморский край": "/region/dfo/primorskij-kraj",
    "ярославль": "/region/cfo/yaroslavskaya-oblast",
    "томск": "/region/sfo/tomskaya-oblast",
    "оренбург": "/region/pfo/orenburgskaya-oblast",
    "кемерово": "/region/sfo/kemerovskaya-oblast",
    "новокузнецк": "/region/sfo/kemerovskaya-oblast",
    "рязань": "/region/cfo/ryazanskaya-oblast",
    "астрахань": "/region/yufo/astrahanskaya-oblast",
    "пенза": "/region/pfo/penzenskaya-oblast",
    "липецк": "/region/cfo/lipetskaya-oblast",
    "тула": "/region/cfo/tulskaya-oblast",
    "киров": "/region/pfo/kirovskaya-oblast",
    "чебоксары": "/region/pfo/chuvashiya",
    "курск": "/region/cfo/kurskaya-oblast",
    "белгород": "/region/cfo/belgorodskaya-oblast",
    "нижний новгород": "/region/pfo/nizhegorodskaya-oblast",
    "ижевск": "/region/pfo/udmurtskaya-respublika",
    "махачкала": "/region/skfo/dagestan-respublika",
    "ставрополь": "/region/skfo/stavropolskij-kraj",
    "ставропольский край": "/region/skfo/stavropolskij-kraj",
    "калининград": "/region/szfo/kaliningradskaya-oblast",
    "мурманск": "/region/szfo/murmanskaya-oblast",
    "вологда": "/region/szfo/vologodskaya-oblast",
    "смоленск": "/region/cfo/smolenskaya-oblast",
    "тамбов": "/region/cfo/tambovskaya-oblast",
    "брянск": "/region/cfo/bryanskaya-oblast",
    "иваново": "/region/cfo/ivanovskaya-oblast",
    "тверь": "/region/cfo/tverskaya-oblast",
    "владимир": "/region/cfo/vladimirskaya-oblast",
    "калуга": "/region/cfo/kaluzhskaya-oblast",
    "кострома": "/region/cfo/kostromskaya-oblast",
    "орёл": "/region/cfo/orlovskaya-oblast",
    "орел": "/region/cfo/orlovskaya-oblast",
    "псков": "/region/szfo/pskovskaya-oblast",
    "великий новгород": "/region/szfo/novgorodskaya-oblast",
    "чита": "/region/sfo/zabajkalskij-kraj",
    "улан-удэ": "/region/sfo/buryatiya-respublika",
    "якутск": "/region/dfo/yakutiya",
    "магадан": "/region/dfo/magadanskaya-oblast",
    "южно-сахалинск": "/region/dfo/sahalinskaya-oblast",
    "симферополь": "/region/yufo/krym-respublika",
    "крым": "/region/yufo/krym-respublika",
    "севастополь": "/region/ufo/sevastopol",
    "горно-алтайск": "/region/sfo/altaj-respublika",
    "абакан": "/region/sfo/hakasiya-respublika",
    "грозный": "/region/skfo/chechenskaya-respublika",
    "нальчик": "/region/skfo/kabardino-balkarskaya-respublika",
    "владикавказ": "/region/skfo/severnaya-osetiya",
    "майкоп": "/region/yufo/adygeya-respublika",
    "петрозаводск": "/region/szfo/kareliya-respublika",
    "сыктывкар": "/region/szfo/komi-respublika",
    "тюмень": "/region/ufo/tyumenskaya-oblast",
    "курган": "/region/ufo/kurganskaya-oblast",
    "благовещенск": "/region/dfo/amurskaya-oblast",
    "хабаровский край": "/region/dfo/habarovskij-kraj",
}

def _city_to_region_path(city):
    """Возвращает путь к странице региона на zakupki360.ru или None."""
    key = city.lower().strip()
    return CITY_REGION_MAP.get(key)

def _parse_tender_cards(soup, max_budget, city_lower=None):
    """Парсит карточки тендеров из BeautifulSoup объекта."""
    tenders = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        if '/tender/' not in href or href in seen:
            continue
        title = a.get_text().strip()
        if len(title) < 10:
            continue
        seen.add(href)

        card_text = ''
        node = a.parent
        for _ in range(6):
            if node is None:
                break
            t = node.get_text(' | ')
            if '₽' in t and any(w in t.lower() for w in
                                 ('область', 'край', 'республика', 'округ', 'москва', 'петербург',
                                  'автономн', 'федеральн')):
                card_text = t
                break
            node = node.parent

        budget_str = region = date = None
        for part in card_text.split('|'):
            p = part.strip()
            if not budget_str and re.search(r'[\d\s]{3,}₽', p):
                budget_str = p
            if not date and re.match(r'\d{2}\.\d{2}\.\d{4}', p):
                date = p
            if not region and any(w in p.lower() for w in
                                  ('область', 'край', 'республика', 'округ', 'москва', 'петербург')):
                region = p

        amount = _parse_amount(budget_str) if budget_str else 0
        if max_budget and amount > max_budget:
            continue

        tenders.append({
            'title': title,
            'url': 'https://zakupki360.ru' + href,
            'amount': amount,
            'budget_str': budget_str or '—',
            'region': region or '—',
            'date': date or '—',
            'source': 'real',
        })
    return tenders

def _fetch_real_tenders(query, max_budget, city=None):
    region_path = _city_to_region_path(city) if city else None

    if region_path:
        # Поиск строго по региону
        url = f"https://zakupki360.ru{region_path}?q={requests.utils.quote(query)}&per_page=30"
    else:
        # Fallback: общий поиск с городом в запросе
        q = f"{city} {query}" if city else query
        url = f"https://zakupki360.ru/search?q={requests.utils.quote(q)}&per_page=30"

    r = requests.get(url, headers=PARSE_HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')
    tenders = _parse_tender_cards(soup, max_budget)
    return tenders[:6]

async def search_tenders_real(amount, profile, query=None, city=None):
    if query is None:
        query = _budget_keyword(amount)
    if city:
        query = f"{city} {query}"
    loop = asyncio.get_event_loop()
    tenders = await loop.run_in_executor(None, _fetch_real_tenders, query, amount, city)
    return tenders

def _fetch_tender_page(url):
    r = requests.get(url, headers=PARSE_HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')
    lines = [l.strip() for l in soup.get_text('\n').split('\n') if len(l.strip()) > 5]
    skip = {'Поиск Закупок', 'Регистрация/вход', 'На контроль', 'Подача заявок',
            'К источнику', 'Мы используем Cookies', 'Карта сайта', 'По отраслям',
            'По регионам', 'По площадкам', 'По заказчикам', 'По ключевым словам',
            'Поиск закупок', 'пн-пт с 9:00 до 18:00', 'Похожие закупки',
            'Тарифы', 'Блог', 'Аутсорсинг', 'Главная', 'Регион', 'Опубликована',
            'Закупка', 'Начальная цена', 'API'}
    cleaned = [l for l in lines if l not in skip and not l.startswith('info@')
               and not l.startswith('8(800)') and '░' not in l
               and 'ИНН' not in l and 'ОГРН' not in l and 'Cookies' not in l
               and 'zakupki360' not in l.lower() and not l.startswith('Адрес:')]
    return '\n'.join(cleaned[:100])

def _fetch_tender_page_detailed(url):
    """Расширенный парсинг: извлекает документы, тип закупки, площадку и полную структуру."""
    r = requests.get(url, headers=PARSE_HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')
    raw_text = r.text

    # --- Документы ---
    docs = []
    doc_section_start = raw_text.find('Документы')
    if doc_section_start > 0:
        doc_html = raw_text[doc_section_start:doc_section_start + 5000]
        doc_soup = BeautifulSoup(doc_html, 'html.parser')
        for div in doc_soup.find_all('div', class_=lambda c: c and 'title' in str(c)):
            txt = div.get_text().strip()
            if txt and len(txt) > 2 and txt != 'Документы':
                docs.append(txt)

    # --- Структурированные поля ---
    lines = [l.strip() for l in soup.get_text('\n').split('\n') if len(l.strip()) > 2]
    skip = {'Поиск Закупок', 'Регистрация/вход', 'На контроль', 'Подача заявок',
            'К источнику', 'Мы используем Cookies', 'Карта сайта', 'По отраслям',
            'По регионам', 'По площадкам', 'По заказчикам', 'По ключевым словам',
            'Поиск закупок', 'пн-пт с 9:00 до 18:00', 'Похожие закупки',
            'Тарифы', 'Блог', 'Аутсорсинг', 'Главная', 'Регион', 'Опубликована',
            'Закупка', 'API', 'На контроль'}
    cleaned = [l for l in lines if l not in skip and not l.startswith('info@')
               and not l.startswith('8(800)') and '░' not in l
               and 'ИНН' not in l and 'ОГРН' not in l and 'Cookies' not in l
               and 'zakupki360' not in l.lower() and not l.startswith('Адрес:')]

    # Убираем дубли, сохраняем порядок
    seen = set()
    unique = []
    for l in cleaned:
        if l not in seen:
            seen.add(l)
            unique.append(l)

    result = '\n'.join(unique[:80])
    if docs:
        unique_docs = list(dict.fromkeys(docs))  # убрать дубли
        result += f"\n\nДОКУМЕНТЫ ТЕНДЕРА:\n" + '\n'.join(f"• {d}" for d in unique_docs)
    return result

def _build_tender_text(tender):
    """Формирует текст тендера для AI из AI-сгенерированного тендера."""
    return (
        f"Название: {tender.get('title')}\n"
        f"Заказчик: {tender.get('customer', '—')}\n"
        f"Сумма: {tender.get('amount', '—')}₽\n"
        f"Регион: {tender.get('region', '—')}\n"
        f"Срок: {tender.get('deadline', '—')}\n"
        f"Описание: {tender.get('description', '—')}\n"
        f"Требования: {tender.get('requirements', '—')}"
    )

async def analyze_tender_by_ai(tender, user_id):
    if tender.get('source') == 'real' and tender.get('url'):
        loop = asyncio.get_event_loop()
        page_text = await loop.run_in_executor(None, _fetch_tender_page, tender['url'])
    else:
        page_text = _build_tender_text(tender)

    prompt = f"""Ты — эксперт по государственным закупкам, помогающий новичкам.

Вот информация о тендере:
---
{page_text}
---

Объясни этот тендер простым языком. Структура ответа:

1. 📌 О ЧЁМ ТЕНДЕР — что именно нужно сделать/поставить, 2-3 предложения простыми словами
2. 💰 ДЕНЬГИ — сумма контракта, как она выплачивается
3. ⏰ СРОКИ — когда подавать заявку, когда нужно выполнить работу
4. 🏛 КТО ЗАКАЗЧИК — кто нанимает и где находится
5. 📋 ЧТО НУЖНО ДЛЯ УЧАСТИЯ — документы, лицензии, опыт
6. ⚠️ РИСКИ — на что обратить внимание новичку
7. ✅ СТОИТ ЛИ УЧАСТВОВАТЬ — честная оценка: легко/средне/сложно для новичка и почему

Пиши коротко, без канцелярита. Максимум 400 слов."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": build_system_prompt(user_id)},
            {"role": "user", "content": prompt}
        ],
        temperature=0.5
    )
    return response.choices[0].message.content

async def analyze_tender_detailed_by_ai(tender, user_id):
    """Детальный разбор: парсим расширенные данные страницы + GPT даёт пошаговый план."""
    if tender.get('source') == 'real' and tender.get('url'):
        loop = asyncio.get_event_loop()
        page_text = await loop.run_in_executor(None, _fetch_tender_page_detailed, tender['url'])
    else:
        page_text = _build_tender_text(tender)

    prompt = f"""Ты — опытный тендерный специалист с 10 годами практики.

Перед тобой полные данные тендера (включая список документов если указан):
---
{page_text}
---

Сделай ДЕТАЛЬНЫЙ разбор для человека, который хочет участвовать в этом тендере впервые.

Формат ответа (строго по разделам):

📁 ДОКУМЕНТЫ ТЕНДЕРА
Перечисли документы из тендера и объясни что в каждом из них обычно написано (ИЗВЕЩЕНИЕ, ТЗ, Смета, Конкурсная документация и т.д.). Если документы не указаны — опиши стандартный набор для данного типа закупки.

📋 ЧТО НУЖНО ПОДГОТОВИТЬ
Полный список документов от участника для подачи заявки (с пояснением каждого пункта).

🏗 НУЖНЫ ЛИ ЛИЦЕНЗИИ / СРО
Укажи конкретно: нужно ли членство в СРО, какие лицензии, допуски — для данного вида работ/поставки.

📅 ПОШАГОВЫЙ ПЛАН УЧАСТИЯ
Нумерованный список шагов от сегодня до подачи заявки. Конкретные действия с примерными сроками.

💸 ФИНАНСОВЫЙ РАСЧЁТ
• Обеспечение заявки (обычно 0.5–5% от НМЦ)
• Обеспечение контракта (обычно 5–30%)
• Возможные расходы на участие
• Примерная маржа при победе

⚠️ ТИПИЧНЫЕ ОШИБКИ НОВИЧКОВ
3–5 частых ошибок при участии в похожих тендерах. Как их избежать.

🎯 ЭКСПЕРТНАЯ ОЦЕНКА
Честный вывод: насколько реально выиграть новичку, что усиливает и ослабляет позицию. Конкретный совет.

Пиши чётко, без воды. Используй числа и факты где возможно."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": build_system_prompt(user_id)},
            {"role": "user", "content": prompt}
        ],
        temperature=0.4,
        max_tokens=1800
    )
    return response.choices[0].message.content

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
            has_no_company = "нет" in profile.get("company", "").lower() or "❌" in profile.get("company", "")
            if has_no_company:
                keyboard.append(["📌 Этап №1"])
            keyboard += [
                ["📊 Мой профиль"],
                ["🎯 Найти тендер"],
                ["📄 Анализ тендера"],
                ["💬 Спросить ИИ"]
            ]
            if user_id and get_active_saved_tenders(user_id):
                keyboard.append(["📁 Мои тендеры"])
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
SAVED_TENDERS_FILE = "saved_tenders.json"
SAVED_TENDER_TTL_HOURS = 72

def load_profiles():
    if os.path.exists(PROFILES_FILE):
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    return {}

def save_profiles():
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in user_profile.items()}, f, ensure_ascii=False, indent=2)

def load_saved_tenders():
    if os.path.exists(SAVED_TENDERS_FILE):
        with open(SAVED_TENDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    return {}

def persist_saved_tenders():
    with open(SAVED_TENDERS_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in user_saved_tenders.items()}, f, ensure_ascii=False, indent=2)

def get_active_saved_tenders(user_id):
    """Возвращает тендеры пользователя, которые ещё не истекли (< 24 ч)."""
    now = datetime.now(timezone.utc)
    entries = user_saved_tenders.get(user_id, [])
    active = []
    for e in entries:
        try:
            saved_at = datetime.fromisoformat(e["saved_at"])
            if (now - saved_at).total_seconds() < SAVED_TENDER_TTL_HOURS * 3600:
                active.append(e)
        except Exception:
            pass
    return active

def add_saved_tender(user_id, tender):
    """Сохраняет тендер для пользователя. Не дублирует по url."""
    now = datetime.now(timezone.utc).isoformat()
    entries = user_saved_tenders.get(user_id, [])
    url = tender.get("url", "")
    if url and any(e["tender"].get("url") == url for e in entries):
        return False
    entries.append({"tender": tender, "saved_at": now, "status": "won"})
    user_saved_tenders[user_id] = entries
    persist_saved_tenders()
    return True

user_histories = {}
user_state = {}
user_profile = load_profiles()
user_saved_tenders = load_saved_tenders()
user_tender_results = {}
user_tender_amount = {}
user_tender_city = {}

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

    city_line = f"\n🏙 Город: {profile.get('city', '-')}\n" if profile.get('city') else "\n🏙 Город: не указан\n"
    return f"""
📊 ТВОЙ ПРОФИЛЬ

🌍 Страна: {profile.get('country', '-')}
{city_line}
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

def parse_budget_max(profile):
    budget = profile.get("budget", "")
    for marker, amount in [("500", 500000), ("300", 300000), ("100", 100000), ("50", 50000)]:
        if marker in budget.replace(" ", ""):
            return amount
    return 100000

def tender_search_inline_kb(user_id):
    profile = user_profile.get(user_id, {})
    max_amount = parse_budget_max(profile)
    steps = list(range(50000, max_amount + 1, 50000))
    buttons = []
    row = []
    for i, amount in enumerate(steps):
        label = f"до {amount // 1000} 000₽"
        row.append(InlineKeyboardButton(label, callback_data=f"tender_budget_{amount}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ Другая сумма", callback_data="tender_custom_amount")])
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(buttons)

def get_tender_advice_by_amount(amount, profile):
    company = profile.get("company", "")
    experience = profile.get("experience", "")
    has_company = "ип" in company.lower() or "ооо" in company.lower()
    has_experience = "есть опыт" in experience.lower() or "✅" in experience

    if amount <= 100000:
        directions = ["🧹 Уборка помещений", "🌱 Мелкое благоустройство", "📋 Мелкие услуги"]
    elif amount <= 200000:
        directions = ["🔧 Техобслуживание", "📦 Мелкая поставка товаров", "🖨 Канцтовары и расходники"]
    elif amount <= 300000:
        directions = ["🌿 Благоустройство", "🔨 Мелкий ремонт помещений", "📦 Поставка оборудования"]
    elif amount <= 400000:
        directions = ["🏗 Строительные работы", "🚛 Поставка стройматериалов", "🛠 Монтажные работы"]
    else:
        directions = ["🏗 Строительство и ремонт", "🚛 Крупные поставки", "🧹 Клининг крупных объектов"]

    why = "без опыта" if not has_experience else "соответствует твоему опыту"
    company_tip = "Для участия потребуется ИП или ООО." if not has_company else "Ты можешь участвовать со своей компанией."
    fmt = f"{amount:,}".replace(",", " ")
    dirs_text = "".join("👉 " + d + "\n" for d in directions)

    return (
        f"🎯 ТЕНДЕРЫ ДО {fmt}₽\n\n"
        f"Рекомендованные направления:\n\n"
        f"{dirs_text}\n"
        f"💡 Почему:\n"
        f"— низкий порог входа\n"
        f"— {why}\n\n"
        f"📌 {company_tip}\n\n"
        f"🔍 Ищи на zakupki.gov.ru с фильтром НМЦ до {fmt}₽"
    )

async def get_registration_instructions(company_type):
    prompt = f"""Ты — юридический консультант по бизнесу в России.

Дай пошаговую инструкцию по регистрации {company_type} в России в 2024 году.
Инструкция должна быть простой, без лишних терминов.
Укажи: шаги, документы, стоимость, сроки.
Формат: нумерованный список шагов, каждый шаг с кратким пояснением."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

async def search_tenders_by_ai(amount, profile):
    company = profile.get("company", "не указана")
    experience = profile.get("experience", "нет")
    country = profile.get("country", "Россия")
    fmt = f"{amount:,}".replace(",", " ")
    prompt = f"""Ты — эксперт по государственным закупкам России (44-ФЗ).

Сгенерируй 4 реалистичных тендера с суммой до {fmt}₽ для участника со следующим профилем:
- Страна: {country}
- Тип компании: {company}
- Опыт: {experience}

Верни ТОЛЬКО валидный JSON-массив (без пояснений, без markdown) из 4 объектов:
[
  {{
    "title": "Краткое название лота (до 50 символов)",
    "number": "номер закупки в формате 0000000000000000000",
    "customer": "Название заказчика",
    "amount": числовая сумма без символов,
    "region": "Регион",
    "deadline": "дата в формате ДД.ММ.ГГГГ",
    "description": "Подробное описание работ/услуг (3-4 предложения)",
    "requirements": "Требования к участнику (2-3 пункта)"
  }}
]"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def tender_results_inline_kb(tenders, show_back=True):
    buttons = []
    for i, t in enumerate(tenders):
        title = t["title"][:40] + ("…" if len(t["title"]) > 40 else "")
        buttons.append([InlineKeyboardButton(f"📋 {title}", callback_data=f"tender_result_{i}")])
    if show_back:
        buttons.append([InlineKeyboardButton("🔄 Найти другие", callback_data="tender_choose_again")])
        buttons.append([InlineKeyboardButton("⬅️ Изменить сумму", callback_data="tender_back")])
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(buttons)

def format_tender_card(t, idx, total):
    if t.get('source') == 'real':
        amt = t.get('budget_str', '—')
        return (
            f"📋 ТЕНДЕР {idx}/{total}\n\n"
            f"🏷 {t['title']}\n\n"
            f"📍 Регион: {t.get('region', '—')}\n"
            f"💰 НМЦ: {amt}\n"
            f"📅 Опубликован: {t.get('date', '—')}\n\n"
            f"🔗 Подробности и документация:\n{t.get('url', '—')}"
        )
    else:
        amt = f"{int(t['amount']):,}".replace(",", " ")
        return (
            f"📋 ТЕНДЕР {idx}/{total}\n\n"
            f"🏷 {t['title']}\n\n"
            f"🔢 Номер: {t.get('number', '—')}\n"
            f"🏛 Заказчик: {t.get('customer', '—')}\n"
            f"📍 Регион: {t.get('region', '—')}\n"
            f"💰 НМЦ: {amt}₽\n"
            f"⏰ Дедлайн: {t.get('deadline', '—')}\n\n"
            f"📝 Описание:\n{t.get('description', '—')}\n\n"
            f"✅ Требования:\n{t.get('requirements', '—')}\n\n"
            f"🔍 Найти на zakupki.gov.ru → поиск по номеру"
        )

def profile_inline_kb(user_id):
    has_company = bool(user_profile.get(user_id, {}).get("company_name"))
    company_label = "✏️ Редактировать компанию" if has_company else "➕ Добавить компанию"
    has_city = bool(user_profile.get(user_id, {}).get("city"))
    city_label = "🏙 Поменять город" if has_city else "🏙 Указать город"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(company_label, callback_data="profile_edit_company")],
        [InlineKeyboardButton(city_label, callback_data="profile_change_city")],
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

    if state == "tender_city":
        city = update.message.text.strip()
        if len(city) < 2:
            await update.message.reply_text("⚠️ Введи название города, например: *Москва*", parse_mode="Markdown")
            return
        user_tender_city[user_id] = city
        user_profile.setdefault(user_id, {})["city"] = city
        save_profiles()
        user_state[user_id] = None
        await update.message.reply_text(
            f"📍 Город сохранён: *{city}*\n\n💰 Выбери максимальную сумму тендера:",
            parse_mode="Markdown",
            reply_markup=tender_search_inline_kb(user_id)
        )
        return

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
            await safe_reply(update.message, response.choices[0].message.content, reply_markup=main_menu(user_id))
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

    if text == "📌 этап №1":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏦 ООО", callback_data="stage1_choose_ooo"),
             InlineKeyboardButton("🏢 ИП", callback_data="stage1_choose_ip")]
        ])
        await update.message.reply_text(
            "📌 ЭТАП №1 — Регистрация компании\n\nВыбери форму собственности:",
            reply_markup=kb
        )
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
        saved_city = user_profile.get(user_id, {}).get("city")
        if saved_city:
            user_tender_city[user_id] = saved_city
            await update.message.reply_text(
                f"📍 Город: *{saved_city}*\n\n💰 Выбери максимальную сумму тендера:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            await update.message.reply_text("Выбери бюджет 👇", reply_markup=tender_search_inline_kb(user_id))
        else:
            user_state[user_id] = "tender_city"
            await update.message.reply_text(
                "🏙 В каком городе ищем тендеры?\n\nНапиши название города (например: _Москва_, _Краснодар_, _Казань_):",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
        return

    if state == "custom_amount":
        raw = re.sub(r"[^\d]", "", update.message.text)
        if not raw or int(raw) < 10000:
            await update.message.reply_text(
                "⚠️ Введи сумму от 10 000₽. Например: *500000*",
                parse_mode="Markdown"
            )
            return
        amount = int(raw)
        user_tender_amount[user_id] = amount
        user_state[user_id] = None
        fmt = f"{amount:,}".replace(",", " ")
        await update.message.reply_text(
            f"💰 Сумма: до {fmt}₽\n\n🗂 Выбери тему тендера:",
            reply_markup=tender_topic_inline_kb(amount)
        )
        return

    if text == "📄 анализ тендера":
        user_state[user_id] = "analyze"
        await update.message.reply_text("Пришли текст тендера или описание 📄")
        return

    if text == "📁 мои тендеры":
        active = get_active_saved_tenders(user_id)
        if not active:
            await update.message.reply_text(
                "📁 У тебя пока нет сохранённых тендеров.\n\n"
                "Найди тендер, изучи его и нажми *«🚀 Реализовать»* — он сохранится здесь на 72 часа.",
                parse_mode="Markdown",
                reply_markup=main_menu(user_id)
            )
            return
        buttons = []
        for i, entry in enumerate(active):
            t = entry["tender"]
            title = t["title"][:38] + ("…" if len(t["title"]) > 38 else "")
            saved_at = datetime.fromisoformat(entry["saved_at"])
            now = datetime.now(timezone.utc)
            remaining_h = int(SAVED_TENDER_TTL_HOURS - (now - saved_at).total_seconds() / 3600)
            label = f"🏆 {title} ({remaining_h}ч)"
            buttons.append([InlineKeyboardButton(label, callback_data=f"my_tender_view_{i}")])
        buttons.append([InlineKeyboardButton("🗑 Очистить список", callback_data="my_tenders_clear")])
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")])
        await update.message.reply_text(
            f"📁 *МОИ ТЕНДЕРЫ* — {len(active)} шт.\n\nНажми на тендер для просмотра:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
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
            await safe_reply(update.message, response.choices[0].message.content, reply_markup=main_menu(user_id))
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
        user_histories[user_id].append({"role": "assistant", "content": fmt_ai(answer)})

        if len(user_histories[user_id]) > 40:
            user_histories[user_id] = user_histories[user_id][-40:]

        await safe_reply(update.message, answer, reply_markup=main_menu(user_id))

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

    if data == "menu_home":
        await query.message.reply_text("Главное меню 👇", reply_markup=main_menu(user_id))

    elif data == "profile_back":
        await query.message.reply_text("Главное меню 👇", reply_markup=main_menu(user_id))

    elif data == "profile_edit_company":
        user_state[user_id] = "add_company"
        await query.message.reply_text(
            "🏢 Введи название своей компании (например: ООО «Ромашка» или ИП Иванов И.И.):"
        )

    elif data == "profile_change_city":
        user_state[user_id] = "tender_city"
        current_city = user_profile.get(user_id, {}).get("city")
        if current_city:
            await query.message.reply_text(
                f"🏙 Текущий город: *{current_city}*\n\nНапиши новый город:",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text(
                "🏙 Напиши название своего города (например: _Москва_, _Краснодар_, _Казань_):",
                parse_mode="Markdown"
            )

    elif data.startswith("tender_budget_"):
        amount = int(data.split("_")[-1])
        user_tender_amount[user_id] = amount
        fmt = f"{amount:,}".replace(",", " ")
        await query.message.reply_text(
            f"💰 Сумма: до {fmt}₽\n\n🗂 Теперь выбери тему тендера:",
            reply_markup=tender_topic_inline_kb(amount)
        )

    elif data.startswith("tender_topic_"):
        parts = data.split("_")
        amount = int(parts[2])
        topic_key = parts[3]
        profile = user_profile.get(user_id, {})
        fmt = f"{amount:,}".replace(",", " ")
        topic_label, topic_query = TENDER_TOPICS.get(topic_key, ("", _budget_keyword(amount)))
        city = user_tender_city.get(user_id)
        city_label = f" | 📍 {city}" if city else ""
        searching_msg = await query.message.reply_text(
            f"🔍 Ищу тендеры «{topic_label}» до {fmt}₽{city_label}..."
        )
        try:
            tenders = await search_tenders_real(amount, profile, query=topic_query, city=city)
            source_label = "🌐 реальных"
            if not tenders:
                await searching_msg.edit_text("⏳ Парсинг не дал результатов, генерирую через ИИ...")
                tenders = await search_tenders_by_ai(amount, profile)
                source_label = "🤖 (ИИ-примеры)"
            user_tender_results[user_id] = tenders
            await searching_msg.edit_text(
                f"✅ Найдено {len(tenders)} {source_label} тендера\n"
                f"Тема: {topic_label} | до {fmt}₽{city_label}\n\n"
                f"Выбери, чтобы посмотреть подробности:",
                reply_markup=tender_results_inline_kb(tenders)
            )
        except Exception:
            await searching_msg.edit_text(
                "⚠️ Не удалось получить тендеры. Попробуй ещё раз.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Попробовать снова", callback_data=data)
                ]])
            )

    elif data.startswith("tender_result_"):
        idx = int(data.split("_")[-1])
        tenders = user_tender_results.get(user_id, [])
        if not tenders or idx >= len(tenders):
            await query.message.reply_text("⚠️ Тендер не найден. Выполни поиск заново.")
            return
        card = format_tender_card(tenders[idx], idx + 1, len(tenders))
        nav_buttons = []
        if idx > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Пред.", callback_data=f"tender_result_{idx - 1}"))
        if idx < len(tenders) - 1:
            nav_buttons.append(InlineKeyboardButton("След. ➡️", callback_data=f"tender_result_{idx + 1}"))
        kb = []
        if nav_buttons:
            kb.append(nav_buttons)
        kb.append([
            InlineKeyboardButton("🤖 Разобрать тендер", callback_data=f"tender_analyze_{idx}"),
            InlineKeyboardButton("📄 Детально", callback_data=f"tender_detail_{idx}"),
        ])
        kb.append([InlineKeyboardButton("🚀 Реализовать", callback_data=f"tender_realize_{idx}")])
        kb.append([InlineKeyboardButton("📋 К списку тендеров", callback_data="tender_show_list")])
        kb.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")])
        await query.message.reply_text(card, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("tender_analyze_"):
        idx = int(data.split("_")[-1])
        tenders = user_tender_results.get(user_id, [])
        if not tenders or idx >= len(tenders):
            await query.message.reply_text("⚠️ Тендер не найден. Выполни поиск заново.")
            return
        tender = tenders[idx]
        title_short = tender['title'][:50] + ('…' if len(tender['title']) > 50 else '')
        thinking_msg = await query.message.reply_text(
            f"🤖 Анализирую тендер...\n\n_{title_short}_\n\nЭто займёт несколько секунд ⏳",
            parse_mode='Markdown'
        )
        try:
            analysis = await analyze_tender_by_ai(tender, user_id)
            back_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад к тендеру", callback_data=f"tender_result_{idx}")],
                [InlineKeyboardButton("📋 К списку тендеров", callback_data="tender_show_list")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")],
            ])
            await thinking_msg.delete()
            await safe_reply(
                query.message,
                f"🤖 *РАЗБОР ТЕНДЕРА*\n\n{analysis}",
                reply_markup=back_kb
            )
        except RateLimitError:
            await thinking_msg.edit_text("⚠️ Превышен лимит OpenAI. Пополните баланс и попробуйте снова.")
        except Exception:
            await thinking_msg.edit_text("⚠️ Не удалось проанализировать тендер. Попробуй ещё раз.")

    elif data.startswith("tender_detail_"):
        idx = int(data.split("_")[-1])
        tenders = user_tender_results.get(user_id, [])
        if not tenders or idx >= len(tenders):
            await query.message.reply_text("⚠️ Тендер не найден. Выполни поиск заново.")
            return
        tender = tenders[idx]
        title_short = tender['title'][:50] + ('…' if len(tender['title']) > 50 else '')
        thinking_msg = await query.message.reply_text(
            f"📄 Изучаю документацию тендера...\n\n_{title_short}_\n\nСобираю данные и готовлю детальный разбор ⏳",
            parse_mode='Markdown'
        )
        try:
            analysis = await analyze_tender_detailed_by_ai(tender, user_id)
            back_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 Краткий разбор", callback_data=f"tender_analyze_{idx}")],
                [InlineKeyboardButton("⬅️ Назад к тендеру", callback_data=f"tender_result_{idx}")],
                [InlineKeyboardButton("📋 К списку тендеров", callback_data="tender_show_list")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")],
            ])
            await thinking_msg.delete()
            # Telegram limit is 4096 chars — split if needed
            header = f"📄 *ДЕТАЛЬНЫЙ РАЗБОР ТЕНДЕРА*\n\n"
            full_text = header + analysis
            if len(full_text) <= 4096:
                await safe_reply(query.message, full_text, reply_markup=back_kb)
            else:
                # Send in two parts
                mid = len(full_text) // 2
                split_at = full_text.rfind('\n', mid - 200, mid + 200)
                if split_at == -1:
                    split_at = mid
                await safe_reply(query.message, full_text[:split_at])
                await safe_reply(query.message, full_text[split_at:].strip(), reply_markup=back_kb)
        except RateLimitError:
            await thinking_msg.edit_text("⚠️ Превышен лимит OpenAI. Пополните баланс и попробуйте снова.")
        except Exception:
            await thinking_msg.edit_text("⚠️ Не удалось выполнить детальный анализ. Попробуй ещё раз.")

    elif data == "tender_show_list":
        tenders = user_tender_results.get(user_id, [])
        if not tenders:
            await query.message.reply_text("⚠️ Список пуст. Выполни поиск заново.",
                reply_markup=tender_search_inline_kb(user_id))
            return
        await query.message.reply_text(
            "📋 Выбери тендер:",
            reply_markup=tender_results_inline_kb(tenders)
        )

    elif data in ("stage1_choose_ooo", "stage1_choose_ip"):
        company_type = "ООО" if data == "stage1_choose_ooo" else "ИП"
        loading_msg = await query.message.reply_text(f"⏳ Формирую инструкцию по регистрации {company_type}...")
        try:
            instructions = await get_registration_instructions(company_type)
            done_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Готово", callback_data="stage1_done")
            ]])
            await safe_edit(
                loading_msg,
                f"📋 *Инструкция по регистрации {company_type}*\n\n{instructions}",
                reply_markup=done_kb
            )
        except Exception:
            await loading_msg.edit_text("⚠️ Не удалось получить инструкцию. Попробуй ещё раз.")

    elif data == "stage1_done":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Этап №2", callback_data="stage2")],
            [InlineKeyboardButton("⬅️ Вернуться назад", callback_data="stage1_back")]
        ])
        await query.message.reply_text(
            "✅ Отлично! Что дальше?",
            reply_markup=kb
        )

    elif data == "stage1_back":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Этап №1", callback_data="stage1_open")],
            [InlineKeyboardButton("➡️ Следующий этап", callback_data="stage2")]
        ])
        await query.message.reply_text(
            "Выбери действие:",
            reply_markup=kb
        )

    elif data == "stage1_open":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏦 ООО", callback_data="stage1_choose_ooo"),
             InlineKeyboardButton("🏢 ИП", callback_data="stage1_choose_ip")]
        ])
        await query.message.reply_text(
            "📌 ЭТАП №1 — Регистрация компании\n\nВыбери форму собственности:",
            reply_markup=kb
        )

    elif data == "stage2":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Этап №2", callback_data="stage2_open")]
        ])
        await query.message.edit_reply_markup(reply_markup=kb)

    elif data == "tender_choose_again":
        amount = user_tender_amount.get(user_id)
        if amount:
            fmt = f"{amount:,}".replace(",", " ")
            await query.message.reply_text(
                f"💰 Сумма: до {fmt}₽\n\n🗂 Выбери другую тему тендера:",
                reply_markup=tender_topic_inline_kb(amount)
            )
        else:
            await query.message.reply_text(
                "💰 Выбери максимальную сумму тендера:",
                reply_markup=tender_search_inline_kb(user_id)
            )

    elif data.startswith("tender_realize_"):
        idx = int(data.split("_")[-1])
        tenders = user_tender_results.get(user_id, [])
        if not tenders or idx >= len(tenders):
            await query.message.reply_text("⚠️ Тендер не найден. Выполни поиск заново.")
            return
        tender = tenders[idx]
        is_new = add_saved_tender(user_id, tender)
        title = tender["title"][:60] + ("…" if len(tender["title"]) > 60 else "")
        if is_new:
            saved_msg = (
                f"🏆 *ТЕНДЕР СОХРАНЁН*\n\n"
                f"_{title}_\n\n"
                f"💾 Тендер добавлен в *«Мои тендеры»* и будет доступен *72 часа*.\n"
                f"Ты найдёшь его в главном меню → 📁 Мои тендеры.\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 *ЧТО ДАЛЬШЕ?*\n\n"
                f"1️⃣ Изучи конкурсную документацию полностью\n"
                f"2️⃣ Подготовь все документы заранее (не в последний день)\n"
                f"3️⃣ Внеси обеспечение заявки на площадку\n"
                f"4️⃣ Подай заявку строго до дедлайна\n"
                f"5️⃣ Участвуй в торгах — снижай цену пошагово\n"
                f"6️⃣ После победы — подпиши контракт и внеси обеспечение исполнения\n\n"
                f"💡 Используй *«📄 Детально»* для пошагового плана по этому тендеру."
            )
        else:
            saved_msg = (
                f"✅ Этот тендер уже есть в *«Мои тендеры»*\n\n"
                f"_{title}_\n\n"
                f"Найди его в главном меню → 📁 Мои тендеры."
            )
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 Мои тендеры", callback_data="my_tenders")],
            [InlineKeyboardButton("⬅️ Назад к тендеру", callback_data=f"tender_result_{idx}")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")],
        ])
        await safe_reply(query.message, saved_msg, reply_markup=back_kb)

    elif data == "my_tenders":
        active = get_active_saved_tenders(user_id)
        if not active:
            await query.message.reply_text(
                "📁 У тебя пока нет сохранённых тендеров.\n\n"
                "Нажми *«🚀 Реализовать»* на карточке тендера, чтобы сохранить его.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎯 Найти тендер", callback_data="tender_find")
                ]])
            )
            return
        buttons = []
        for i, entry in enumerate(active):
            t = entry["tender"]
            title = t["title"][:38] + ("…" if len(t["title"]) > 38 else "")
            saved_at = datetime.fromisoformat(entry["saved_at"])
            now = datetime.now(timezone.utc)
            remaining_h = int(SAVED_TENDER_TTL_HOURS - (now - saved_at).total_seconds() / 3600)
            label = f"🏆 {title} ({remaining_h}ч)"
            buttons.append([InlineKeyboardButton(label, callback_data=f"my_tender_view_{i}")])
        buttons.append([InlineKeyboardButton("🗑 Очистить список", callback_data="my_tenders_clear")])
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")])
        await query.message.reply_text(
            f"📁 *МОИ ТЕНДЕРЫ* — {len(active)} шт.\n\n"
            f"Тендеры хранятся 72 часа. Нажми на тендер для просмотра:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("my_tender_view_"):
        saved_idx = int(data.split("_")[-1])
        active = get_active_saved_tenders(user_id)
        if saved_idx >= len(active):
            await query.message.reply_text("⚠️ Тендер не найден или истёк срок хранения.")
            return
        entry = active[saved_idx]
        t = entry["tender"]
        saved_at = datetime.fromisoformat(entry["saved_at"])
        now = datetime.now(timezone.utc)
        remaining_h = int(SAVED_TENDER_TTL_HOURS - (now - saved_at).total_seconds() / 3600)
        title = t.get("title", "—")
        budget = t.get("budget_str", "—")
        region = t.get("region", "—")
        date = t.get("date", "—")
        url = t.get("url", "")
        card = (
            f"🏆 *ВЫИГРАННЫЙ ТЕНДЕР*\n\n"
            f"📌 {title}\n\n"
            f"💰 Сумма: {budget}\n"
            f"📍 Регион: {region}\n"
            f"📅 Дата: {date}\n\n"
            f"⏳ Хранится ещё: ~{remaining_h} ч.\n"
        )
        if url:
            card += f"🔗 [Открыть на zakupki360.ru]({url})"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 Детально", callback_data=f"my_tender_detail_{saved_idx}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data="my_tenders")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")],
        ])
        try:
            await query.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            await query.message.reply_text(card, reply_markup=kb)

    elif data.startswith("my_tender_detail_"):
        saved_idx = int(data.split("_")[-1])
        active = get_active_saved_tenders(user_id)
        if saved_idx >= len(active):
            await query.message.reply_text("⚠️ Тендер не найден или истёк срок хранения.")
            return
        tender = active[saved_idx]["tender"]
        title_short = tender["title"][:50] + ("…" if len(tender["title"]) > 50 else "")
        thinking_msg = await query.message.reply_text(
            f"📄 Изучаю документацию тендера...\n\n_{title_short}_\n\nСобираю данные и готовлю детальный разбор ⏳",
            parse_mode="Markdown"
        )
        try:
            analysis = await analyze_tender_detailed_by_ai(tender, user_id)
            back_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад к тендеру", callback_data=f"my_tender_view_{saved_idx}")],
                [InlineKeyboardButton("📁 Мои тендеры", callback_data="my_tenders")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")],
            ])
            await thinking_msg.delete()
            header = "📄 *ДЕТАЛЬНЫЙ РАЗБОР ТЕНДЕРА*\n\n"
            full_text = header + analysis
            if len(full_text) > 4096:
                await safe_reply(query.message, full_text[:4096], reply_markup=None)
                await safe_reply(query.message, full_text[4096:], reply_markup=back_kb)
            else:
                await safe_reply(query.message, full_text, reply_markup=back_kb)
        except RateLimitError:
            await thinking_msg.edit_text("⚠️ Превышен лимит OpenAI. Пополните баланс и попробуйте снова.")
        except Exception:
            await thinking_msg.edit_text("⚠️ Не удалось проанализировать тендер. Попробуй ещё раз.")

    elif data == "my_tenders_clear":
        user_saved_tenders[user_id] = []
        persist_saved_tenders()
        await query.message.reply_text(
            "🗑 Список тендеров очищен.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")
            ]])
        )

    elif data == "tender_find":
        await query.message.reply_text(
            "💰 Выбери максимальную сумму тендера:",
            reply_markup=tender_search_inline_kb(user_id)
        )

    elif data == "tender_custom_amount":
        user_state[user_id] = "custom_amount"
        await query.message.reply_text(
            "✏️ Напиши сумму тендера в рублях (например: *500000* или *1 500 000*):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Главное меню", callback_data="menu_home")
            ]])
        )

    elif data == "tender_back":
        await query.message.reply_text(
            "💰 Выбери максимальную сумму тендера:",
            reply_markup=tender_search_inline_kb(user_id)
        )

app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(CallbackQueryHandler(callback_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.job_queue.run_daily(send_daily_tip, time=dtime(hour=6, minute=0, tzinfo=timezone.utc))

app.run_polling()
