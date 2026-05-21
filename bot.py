"""
MiK Real Estate Bot — полный автомат
✅ Парсинг krisha.kz + OLX
✅ AI агент (отвечает клиентам)
✅ Воронка продаж (Лид → Встреча → Сделка)
✅ CRM + статистика
✅ Контент завод (Telegram, Instagram, YouTube, TikTok)
✅ Авто follow-up напоминания
"""
import logging, json, os, re, asyncio
import psycopg2, psycopg2.extras, httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import (Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton)
from telegram.ext import (Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, ConversationHandler)
from aiohttp import web
async def health(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()
# ══════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════
BOT_TOKEN   = os.getenv("BOT_TOKEN")
ADMIN_ID    = int(os.getenv("ADMIN_CHAT_ID", "0"))
DB_URL      = os.getenv("DATABASE_URL", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CHANNEL_ID  = os.getenv("CHANNEL_ID", "")  # @your_channel или -100xxx

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
ai = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}

# Шаги диалога для подачи объявления
(STEP_TYPE, STEP_ROOMS, STEP_DISTRICT, STEP_ADDRESS,
 STEP_AREA, STEP_FLOOR, STEP_PRICE, STEP_DESC,
 STEP_PHOTOS, STEP_CONTACT, STEP_CONFIRM) = range(11)

DISTRICTS = ["Есиль", "Алматы", "Байконур", "Сарыарка", "Нура", "Целиноградский", "Другой"]
TYPES     = ["Квартира", "Дом", "Участок", "Коммерция"]
ROOMS     = ["Студия", "1", "2", "3", "4+"]

# Воронка продаж
FUNNEL = ["🆕 Лид", "📞 Контакт", "👁 Показ", "🤝 Переговоры", "✅ Сделка", "❌ Отказ"]
FUNNEL_KEYS = ["лид", "контакт", "показ", "переговоры", "сделка", "отказ"]
# ══════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════
def get_db():
    return psycopg2.connect(
        DB_URL.replace("postgres://", "postgresql://"),
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def init_db():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    date TEXT,
                    type TEXT,
                    rooms TEXT,
                    district TEXT,
                    address TEXT,
                    area TEXT,
                    floor TEXT,
                    price TEXT,
                    description TEXT,
                    photos JSONB DEFAULT '[]',
                    contact_phone TEXT,
                    contact_name TEXT,
                    tg_username TEXT,
                    tg_id BIGINT,
                    crm_status TEXT DEFAULT 'лид',
                    funnel_stage TEXT DEFAULT 'лид',
                    notes TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    follow_up_date TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS parsed_ids (
                    external_id TEXT PRIMARY KEY,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS ai_chats (
                    tg_id BIGINT PRIMARY KEY,
                    history JSONB DEFAULT '[]',
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)
        conn.commit()

def sql(query, params=(), fetch=None):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(query, params)

            if fetch == "one":
                return c.fetchone()

            if fetch == "all":
                return c.fetchall()

            conn.commit()

def add_listing(l):
    sql("""
        INSERT INTO listings (
            id,date,type,rooms,district,address,area,floor,
            price,description,photos,contact_phone,
            contact_name,tg_username,tg_id,
            crm_status,funnel_stage,notes,source,updated_at
        )
        VALUES (
            %(id)s,%(date)s,%(type)s,%(rooms)s,%(district)s,
            %(address)s,%(area)s,%(floor)s,%(price)s,
            %(description)s,%(photos)s,%(contact_phone)s,
            %(contact_name)s,%(tg_username)s,%(tg_id)s,
            %(crm_status)s,%(funnel_stage)s,%(notes)s,
            %(source)s,%(updated_at)s
        )
        ON CONFLICT (id) DO NOTHING
    """, {
        **l,
        "photos": json.dumps(l.get("photos", []), ensure_ascii=False),
        "notes": l.get("notes", ""),
        "source": l.get("source", "manual"),
        "funnel_stage": l.get("funnel_stage", "лид"),
        "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M")
    })

def load_db():
    return sql(
        "SELECT * FROM listings ORDER BY date DESC",
        fetch="all"
    ) or []

def update_listing(lid, **kwargs):
    kwargs["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    sets = ", ".join(f"{k}=%s" for k in kwargs)
    sql(
        f"UPDATE listings SET {sets} WHERE id=%s",
        list(kwargs.values()) + [lid]
    )

def is_parsed(eid):
    return bool(
        sql(
            "SELECT 1 FROM parsed_ids WHERE external_id=%s",
            (eid,),
            fetch="one"
        )
    )

def mark_parsed(eid, src):
    sql(
        "INSERT INTO parsed_ids(external_id,source) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (eid, src)
    )

def get_history(tg_id):
    r = sql(
        "SELECT history FROM ai_chats WHERE tg_id=%s",
        (tg_id,),
        fetch="one"
    )
    return r["history"] if r else []

def save_history(tg_id, h):
    sql("""
        INSERT INTO ai_chats(tg_id,history)
        VALUES(%s,%s)
        ON CONFLICT(tg_id)
        DO UPDATE SET history=%s, updated_at=NOW()
    """, (
        tg_id,
        json.dumps(h, ensure_ascii=False),
        json.dumps(h, ensure_ascii=False)
    ))

# ══════════════════════════════════════════════════
# AI АГЕНТ
# ══════════════════════════════════════════════════
AGENT_SYSTEM = """Ты — AI агент MiK Real Estate, топового агентства недвижимости Астаны.

Твои задачи:
1. Выявить потребность: аренда или покупка, бюджет, район, комнаты
2. Предложить подходящие варианты от собственников 
3. Назначить показ — спросить удобное время
4. Получить номер телефона у заинтересованного клиента

Стиль: дружелюбный, профессиональный, краткие ответы.
Язык: русский.
Если клиент называет бюджет — сразу предложи 2-3 варианта района.
Если хочет смотреть — попроси телефон и удобное время."""

async def ai_reply(tg_id: int, text: str) -> str:
    if not ai:
        return "Здравствуйте! Я помогу подобрать недвижимость в Астане. Что вас интересует — аренда или покупка? 🏠"
    history = get_history(tg_id)
    history.append({"role": "user", "content": text})
    if len(history) > 12: history = history[-12:]
    try:
            r = ai.messages.create(
                model="claude-haiku-4-5-20251001",
                system=AGENT_SYSTEM,
                messages=history,
                max_tokens=400)
            reply = r.content[0].text
            history.append({"role":"assistant","content":reply})
            save_history(tg_id, history)
            return reply
    except Exception as e:
            log.error(f"AI error: {e}")
            return "Извините, попробуйте чуть позже. Или напишите напрямую: +77058060781"
async def gen_content(listing: dict) -> dict:
    """Генерирует контент для всех платформ."""
    info = (f"Объект: {listing.get('type','Квартира')} {listing.get('rooms','')} комн.\n"
            f"Адрес: {listing.get('address','')}, район {listing.get('district','')}\n"
            f"Площадь: {listing.get('area','')} м², этаж: {listing.get('floor','')}\n"
            f"Цена: {listing.get('price','')} ₸\n"
            f"Описание: {listing.get('description','')}")

    if not ai:
        stub = f"🏠 {listing.get('type','')} {listing.get('rooms','')} комн.\n📍 {listing.get('address','')}\n💰 {listing.get('price','')} ₸\n📞 Пишите в бот!"
        return {"tg": stub, "instagram": stub, "youtube": "AI недоступен", "tiktok": "AI недоступен"}

    prompts = {
        "tg": f"Продающий пост для Telegram канала недвижимости. До 120 слов. Эмодзи, преимущества, призыв писать в директ.\n\n{info}",
        "instagram": f"Instagram caption для недвижимости. До 80 слов + 15 хэштегов (#астана #недвижимость и др).\n\n{info}",
        "youtube": f"Для YouTube видео об объекте:\n1) Заголовок (до 55 символов, цепляющий)\n2) Описание (150 слов, SEO)\n3) Теги (20 тегов через запятую)\n\n{info}",
        "tiktok": f"Сценарий TikTok 30 сек об объекте недвижимости:\n- Хук (первые 3 сек — интригующий вопрос)\n- Основная часть (показываем объект)\n- Призыв (подписаться + написать)\nРазговорный стиль.\n\n{info}",
    }

    results = {}
    for platform, prompt in prompts.items():
        try:
            r = await ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":prompt}],
                max_tokens=500)
            results[platform] = r.choices[0].message.content
        except Exception as e:
            results[platform] = f"Ошибка: {e}"
        await asyncio.sleep(0.2)
    return results

# ══════════════════════════════════════════════════
# ПАРСЕРЫ
# ══════════════════════════════════════════════════
async def parse_krisha() -> list:
    results = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        for deal, url in [
            ("rent", "https://krisha.kz/arenda/kvartiry/astana/"),
            ("sale", "https://krisha.kz/prodazha/kvartiry/astana/"),
        ]:
            try:
                soup = BeautifulSoup((await c.get(url)).text, "html.parser")
                for card in soup.select("div.a-card"):
                    link = card.select_one("a[href*='/kvartiry/']")
                    if not link: continue
                    href = link.get("href","")
                    m = re.search(r"/(\d+)\.html", href)
                    if not m: continue
                    eid = f"krisha_{m.group(1)}"
                    if is_parsed(eid): continue
                    title = link.get_text(strip=True)
                    pt = card.select_one(".a-card__price")
                    at = card.select_one(".a-card__subtitle")
                    am = re.search(r"(\d+[\.,]?\d*)\s*м²", card.get_text())
                    rm = re.search(r"(\d)-комн", title)
                    results.append({
                        "ext_id": eid, "source": "krisha", "deal_type": deal,
                        "title": title,
                        "price": pt.get_text(strip=True) if pt else "—",
                        "address": at.get_text(strip=True) if at else "—",
                        "area": am.group(1) if am else "—",
                        "rooms": rm.group(1) if rm else "—",
                        "url": "https://krisha.kz" + href,
                    })
            except Exception as e: log.error(f"[krisha] {e}")
    return results

async def parse_olx() -> list:
    results = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as c:
        for deal, cat in [("rent",1181),("sale",1178)]:
            try:
                data = (await c.get("https://www.olx.kz/api/v1/offers/", params={
                    "offset":0,"limit":40,"category_id":cat,
                    "city_id":13,"owner_type":"private","sort_by":"created_at:desc"
                })).json()
                for o in data.get("data",[]):
                    eid = f"olx_{o['id']}"
                    if is_parsed(eid): continue
                    pv = o.get("price",{}).get("value",{}).get("value","—")
                    params = {p["key"]:p.get("value",{}).get("label","") for p in o.get("params",[])}
                    dist = o.get("location",{}).get("district",{}).get("name","")
                    results.append({
                        "ext_id": eid, "source": "olx", "deal_type": deal,
                        "title": o.get("title",""),
                        "price": f"{pv} ₸" if pv != "—" else "—",
                        "address": f"Астана, {dist}".strip(", "),
                        "area": str(params.get("size","—")),
                        "rooms": str(params.get("rooms","—")),
                        "url": o.get("url",""),
                    })
            except Exception as e: log.error(f"[olx] {e}")
    return results

async def run_parsers(bot):
    log.info("⏰ Парсинг...")
    all_items = ((await parse_krisha()) + (await parse_olx()))[:8]
    new = 0
    for item in all_items:
        try:
            lid = datetime.now().strftime("%Y%m%d%H%M%S") + item["ext_id"][-6:]
            deal_label = "Аренда" if item["deal_type"]=="rent" else "Продажа"
            src_label  = "🏠 krisha.kz" if item["source"]=="krisha" else "🛒 OLX.kz"
            listing = {
                "id": lid,
                "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "type": "Квартира", "rooms": item["rooms"],
                "district": "—", "address": item["address"],
                "area": item["area"], "floor": "—",
                "price": item["price"], "description": item["title"],
                "photos": [], "contact_phone": "—",
                "contact_name": "Собственник", "tg_username": "—",
                "tg_id": 0, "crm_status": "лид", "funnel_stage": "лид",
                "notes": "", "source": item["source"],
            }
            add_listing(listing)
            mark_parsed(item["ext_id"], item["source"])

            text = (f"🔔 *Новый лид от собственника*\n"
                    f"{src_label} | {deal_label}\n\n"
                    f"🏠 {item['rooms']} комн. | 📐 {item['area']} м²\n"
                    f"📍 {item['address']}\n"
                    f"💰 {item['price']}\n\n"
                    f"🔗 [Открыть объявление]({item['url']})")
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Контакт",   callback_data=f"funnel_{lid}_контакт"),
                 InlineKeyboardButton("👁 Показ",     callback_data=f"funnel_{lid}_показ")],
                [InlineKeyboardButton("✅ Сделка",    callback_data=f"funnel_{lid}_сделка"),
                 InlineKeyboardButton("❌ Отказ",     callback_data=f"funnel_{lid}_отказ")],
                [InlineKeyboardButton("✍️ Контент",   callback_data=f"content_{lid}"),
                 InlineKeyboardButton("📝 Заметка",   callback_data=f"note_{lid}")],
            ])
            await bot.send_message(ADMIN_ID, text, parse_mode="Markdown",
                                   reply_markup=kb, disable_web_page_preview=True)
            new += 1
            await asyncio.sleep(0.5)
        except Exception as e: log.error(f"Send error: {e}")
    log.info(f"✅ Новых лидов: {new}")

# ══════════════════════════════════════════════════
# FOLLOW-UP НАПОМИНАНИЯ
# ══════════════════════════════════════════════════
async def check_followups(bot):
    """Напоминает о лидах на стадии Контакт и Показ больше 2 дней."""
    listings = load_db()
    today = datetime.now()
    for l in listings:
        if l.get("funnel_stage") not in ["контакт", "показ"]:
            continue
        updated = l.get("updated_at","")
        if not updated: continue
        try:
            upd_date = datetime.strptime(updated, "%d.%m.%Y %H:%M")
            days_ago = (today - upd_date).days
            if days_ago >= 2:
                stage_emoji = "📞" if l["funnel_stage"] == "контакт" else "👁"
                await bot.send_message(ADMIN_ID,
                    f"⏰ *Follow-up напоминание*\n\n"
                    f"{stage_emoji} Стадия: {l['funnel_stage'].upper()}\n"
                    f"🏠 {l.get('type','')} {l.get('rooms','')} комн.\n"
                    f"📍 {l.get('address','')}\n"
                    f"💰 {l.get('price','')} ₸\n"
                    f"📅 Обновлён: {updated} ({days_ago} дн. назад)\n\n"
                    f"Не забудь связаться!",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🤝 Переговоры", callback_data=f"funnel_{l['id']}_переговоры"),
                         InlineKeyboardButton("✅ Сделка",     callback_data=f"funnel_{l['id']}_сделка")],
                        [InlineKeyboardButton("❌ Отказ",      callback_data=f"funnel_{l['id']}_отказ")],
                    ]))
        except: pass

# ══════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════
def rkb(opts, cols=2):
    rows = [opts[i:i+cols] for i in range(0,len(opts),cols)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def is_admin(u): return u.effective_user.id == ADMIN_ID

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Воронка продаж",    callback_data="funnel_view"),
         InlineKeyboardButton("📋 Все лиды",          callback_data="list_all")],
        [InlineKeyboardButton("🆕 Новые лиды",        callback_data="list_лид"),
         InlineKeyboardButton("📞 На контакте",       callback_data="list_контакт")],
        [InlineKeyboardButton("👁 Показы",            callback_data="list_показ"),
         InlineKeyboardButton("🤝 Переговоры",        callback_data="list_переговоры")],
        [InlineKeyboardButton("✅ Сделки",            callback_data="list_сделка"),
         InlineKeyboardButton("📊 Статистика",        callback_data="stats")],
        [InlineKeyboardButton("✍️ Контент завод",     callback_data="content_menu"),
         InlineKeyboardButton("🔍 Парсинг сейчас",   callback_data="parse_now")],
    ])

# ══════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        await update.message.reply_text(
            "🏢 *MiK Real Estate — Центр управления*\n\n"
            "Выберите раздел:",
            parse_mode="Markdown", reply_markup=main_menu_kb())
        return ConversationHandler.END

    ctx.user_data.clear()
    ctx.user_data["photos"] = []
    await update.message.reply_text(
        "👋 Добро пожаловать в *MiK Real Estate*!\n\n"
        "🏠 Недвижимость в Астане от собственников\n"
        "💰 Лучшие предложения рынка\n\n"
        "Чем могу помочь?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([
            ["🏠 Подать объявление"],
            ["💬 Подобрать недвижимость"],
        ], resize_keyboard=True))
    return ConversationHandler.END

async def handle_client(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(update): return ConversationHandler.END
    text = update.message.text or ""

    if text == "🏠 Подать объявление":
        ctx.user_data.clear()
        ctx.user_data["photos"] = []
        await update.message.reply_text("Выберите тип объекта:", reply_markup=rkb(TYPES))
        return STEP_TYPE

    if text == "💬 Подобрать недвижимость":
        await update.message.reply_text(
            "Отлично! Расскажите — аренда или покупка? Какой бюджет? 🏠",
            reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    # AI отвечает на всё
    await update.message.chat.send_action("typing")
    reply = await ai_reply(update.effective_user.id, text)
    await update.message.reply_text(reply)

    # Уведомляем админа
    username = update.effective_user.username or "—"
    name = update.effective_user.first_name or "Клиент"
    try:
        await ctx.bot.send_message(ADMIN_ID,
            f"👤 *Новый клиент:* {name} @{username}\n"
            f"💬 _{text[:80]}_\n"
            f"🤖 AI: _{reply[:80]}_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Создать лид", callback_data=f"newlead_{update.effective_user.id}_{username}")
            ]]))
    except: pass
    return ConversationHandler.END

async def cmd_crm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("🏢 *MiK Real Estate*", parse_mode="Markdown", reply_markup=main_menu_kb())

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    # ── Парсинг ──
    if d == "parse_now":
        await q.edit_message_text("🔍 Парсю krisha.kz и OLX... (~30 сек)")
        await run_parsers(ctx.bot)
        await ctx.bot.send_message(ADMIN_ID, "✅ Парсинг завершён! Новые лиды выше ☝️")
        return

    # ── Главное меню ──
    if d == "menu":
        await q.edit_message_text("🏢 *MiK Real Estate*", parse_mode="Markdown", reply_markup=main_menu_kb())
        return

    # ── Воронка продаж ──
    if d == "funnel_view":
        listings = load_db()
        counts = {k: 0 for k in FUNNEL_KEYS}
        for l in listings:
            s = l.get("funnel_stage","лид")
            if s in counts: counts[s] += 1
        total = len(listings)
        deals = counts.get("сделка",0)
        conv  = round(deals/total*100,1) if total else 0
        text  = "🔥 *Воронка продаж MiK Real Estate*\n\n"
        arrows = ["🆕 Лид", "📞 Контакт", "👁 Показ", "🤝 Переговоры", "✅ Сделка"]
        for i, (label, key) in enumerate(zip(arrows, FUNNEL_KEYS[:5])):
            cnt = counts.get(key, 0)
            bar = "█" * min(cnt, 20)
            text += f"{label}: *{cnt}* {bar}\n"
            if i < 4: text += "      ↓\n"
        text += f"\n❌ Отказов: *{counts.get('отказ',0)}*\n"
        text += f"\n📊 Конверсия: *{conv}%* ({deals} сделок из {total})"
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Меню", callback_data="menu")]]))
        return

    # ── Список лидов ──
    if d.startswith("list_"):
        status = d[5:]
        listings = load_db()
        if status != "all":
            listings = [l for l in listings if l.get("funnel_stage") == status]
        title_map = {"all":"📋 Все лиды","лид":"🆕 Новые","контакт":"📞 Контакт",
                     "показ":"👁 Показы","переговоры":"🤝 Переговоры",
                     "сделка":"✅ Сделки","отказ":"❌ Отказы"}
        title = title_map.get(status, status)
        if not listings:
            await q.edit_message_text(f"*{title}*\n\nПусто 🤷", parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Меню", callback_data="menu")]]))
            return
        src_icon = {"manual":"✍️","krisha":"🏠","olx":"🛒"}
        text = f"*{title}* ({len(listings)})\n\n"
        for i, l in enumerate(listings[:10], 1):
            icon = src_icon.get(l.get("source","manual"),"")
            text += (f"*{i}.* {icon} {l.get('type','')} {l.get('rooms','')} комн.\n"
                     f"📍 {l.get('address','')[:35]}\n"
                     f"💰 {l.get('price','')} | 📅 {l.get('date','')}\n\n")
        if len(listings) > 10:
            text += f"_...ещё {len(listings)-10}_"
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Меню", callback_data="menu")]]))
        return

    # ── Статистика ──
    if d == "stats":
        listings = load_db()
        total = len(listings)
        by_stage = {}
        by_src   = {}
        revenue  = 0
        for l in listings:
            s = l.get("funnel_stage","лид")
            by_stage[s] = by_stage.get(s,0) + 1
            src = l.get("source","manual")
            by_src[src] = by_src.get(src,0) + 1
        deals = by_stage.get("сделка",0)
        conv  = round(deals/total*100,1) if total else 0
        text  = (f"📊 *Статистика MiK Real Estate*\n\n"
                 f"📋 Всего лидов: *{total}*\n"
                 f"✅ Сделок: *{deals}* (конверсия *{conv}%*)\n\n"
                 f"*Воронка:*\n")
        stage_labels = {"лид":"🆕","контакт":"📞","показ":"👁",
                        "переговоры":"🤝","сделка":"✅","отказ":"❌"}
        for key in FUNNEL_KEYS:
            cnt = by_stage.get(key,0)
            text += f"  {stage_labels.get(key,'')} {key}: *{cnt}*\n"
        text += "\n*Источники:*\n"
        src_labels = {"manual":"✍️ Вручную","krisha":"🏠 Krisha","olx":"🛒 OLX"}
        for s,c in by_src.items():
            text += f"  {src_labels.get(s,s)}: *{c}*\n"
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Меню", callback_data="menu")]]))
        return

    # ── Смена стадии воронки ──
    if d.startswith("funnel_"):
        parts = d.split("_", 2)
        if len(parts) == 3:
            _, lid, stage = parts
            update_listing(lid, funnel_stage=stage, crm_status=stage)
            stage_labels = {"лид":"🆕 Лид","контакт":"📞 Контакт","показ":"👁 Показ",
                           "переговоры":"🤝 Переговоры","сделка":"✅ Сделка","отказ":"❌ Отказ"}
            await q.answer(f"✅ {stage_labels.get(stage, stage)}", show_alert=False)
        return

    # ── Заметка ──
    if d.startswith("note_"):
        lid = d[5:]
        ctx.user_data["note_lid"] = lid
        await q.message.reply_text(f"📝 Напиши заметку для лида #{lid[-6:]}:")
        return

    # ── Контент завод ──
    if d == "content_menu":
        listings = load_db()[:8]
        if not listings:
            await q.edit_message_text("Нет объявлений для генерации контента.")
            return
        btns = []
        for l in listings:
            label = f"🏠 {l.get('rooms','')}к {l.get('address','')[:25]}"
            btns.append([InlineKeyboardButton(label, callback_data=f"content_{l['id']}")])
        btns.append([InlineKeyboardButton("◀️ Меню", callback_data="menu")])
        await q.edit_message_text("✍️ *Контент завод*\n\nВыбери объявление:", 
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
        return

    if d.startswith("content_"):
        lid = d[8:]
        listing = sql("SELECT * FROM listings WHERE id=%s", (lid,), fetch="one")
        if not listing:
            await q.answer("Объявление не найдено", show_alert=True)
            return
        await q.edit_message_text("✍️ Генерирую контент для всех платформ... (~15 сек)")
        content = await gen_content(listing)

        # Telegram
        await ctx.bot.send_message(ADMIN_ID,
            f"📱 *TELEGRAM POST*\n\n{content['tg']}", parse_mode="Markdown")
        # Instagram
        await ctx.bot.send_message(ADMIN_ID,
            f"📸 *INSTAGRAM*\n\n{content['instagram']}", parse_mode="Markdown")
        # YouTube
        await ctx.bot.send_message(ADMIN_ID,
            f"▶️ *YOUTUBE*\n\n{content['youtube']}", parse_mode="Markdown")
        # TikTok
        await ctx.bot.send_message(ADMIN_ID,
            f"🎵 *TIKTOK СЦЕНАРИЙ*\n\n{content['tiktok']}", parse_mode="Markdown")

        # Постим в канал если настроен
        if CHANNEL_ID:
            try:
                await ctx.bot.send_message(CHANNEL_ID, content["tg"], parse_mode="Markdown")
                await ctx.bot.send_message(ADMIN_ID, "✅ Пост опубликован в канал!")
            except Exception as e:
                await ctx.bot.send_message(ADMIN_ID, f"⚠️ Не удалось опубликовать в канал: {e}")
        return

    # ── Создать лид из чата с клиентом ──
    if d.startswith("newlead_"):
        parts = d.split("_", 2)
        tg_id  = parts[1] if len(parts) > 1 else "0"
        uname  = parts[2] if len(parts) > 2 else "—"
        lid = datetime.now().strftime("%Y%m%d%H%M%S") + tg_id[-4:]
        listing = {
            "id": lid, "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "type": "—", "rooms": "—", "district": "—", "address": "—",
            "area": "—", "floor": "—", "price": "—", "description": "Из AI чата",
            "photos": [], "contact_phone": "—",
            "contact_name": uname, "tg_username": uname,
            "tg_id": int(tg_id), "crm_status": "лид",
            "funnel_stage": "лид", "notes": "", "source": "ai_chat",
        }
        add_listing(listing)
        await q.answer("✅ Лид создан!", show_alert=True)
        return

# ── Заметка текстом ──
async def handle_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lid = ctx.user_data.get("note_lid")
    if not lid: return
    update_listing(lid, notes=update.message.text[:500])
    ctx.user_data.pop("note_lid", None)
    await update.message.reply_text(f"✅ Заметка сохранена для #{lid[-6:]}")

# ══════════════════════════════════════════════════
# ДИАЛОГ — ПОДАЧА ОБЪЯВЛЕНИЯ
# ══════════════════════════════════════════════════
async def step_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["type"] = update.message.text
    if update.message.text == "Квартира":
        await update.message.reply_text("Сколько комнат?", reply_markup=rkb(ROOMS))
        return STEP_ROOMS
    ctx.user_data["rooms"] = "—"
    await update.message.reply_text("Выберите район:", reply_markup=rkb(DISTRICTS))
    return STEP_DISTRICT

async def step_rooms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["rooms"] = update.message.text
    await update.message.reply_text("Выберите район:", reply_markup=rkb(DISTRICTS))
    return STEP_DISTRICT

async def step_district(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["district"] = update.message.text
    await update.message.reply_text("Адрес (улица, дом, ЖК):", reply_markup=ReplyKeyboardRemove())
    return STEP_ADDRESS

async def step_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["address"] = update.message.text
    await update.message.reply_text("Площадь (м²), например: 65")
    return STEP_AREA

async def step_area(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["area"] = update.message.text
    await update.message.reply_text("Этаж/этажей, например: 5/9")
    return STEP_FLOOR

async def step_floor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["floor"] = update.message.text
    await update.message.reply_text("Цена (₸):")
    return STEP_PRICE

async def step_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["price"] = update.message.text
    await update.message.reply_text("Краткое описание (или напиши —):")
    return STEP_DESC

async def step_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["desc"] = update.message.text
    await update.message.reply_text(
        "📸 Отправьте фото объекта.\nКогда готово — напишите *готово*.",
        parse_mode="Markdown")
    return STEP_PHOTOS

async def step_photos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.lower() == "готово":
        await update.message.reply_text("📞 Ваш контакт:",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True))
        return STEP_CONTACT
    if update.message.photo:
        ctx.user_data["photos"].append({"type":"photo","file_id":update.message.photo[-1].file_id})
        await update.message.reply_text(f"✅ Фото {len(ctx.user_data['photos'])}. Ещё или *готово*.", parse_mode="Markdown")
    elif update.message.video:
        ctx.user_data["photos"].append({"type":"video","file_id":update.message.video.file_id})
        await update.message.reply_text("✅ Видео. Ещё или *готово*.", parse_mode="Markdown")
    return STEP_PHOTOS

async def step_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    phone = update.message.contact.phone_number if update.message.contact else update.message.text
    ctx.user_data.update({
        "phone": phone,
        "name": update.message.from_user.first_name or "",
        "username": update.message.from_user.username or "—",
    })
    d = ctx.user_data
    summary = (f"📋 *Проверьте данные:*\n\n"
               f"🏠 {d.get('type')} {d.get('rooms','—')} комн.\n"
               f"📍 {d.get('district')}, {d.get('address')}\n"
               f"📐 {d.get('area')} м² | 🏢 {d.get('floor')}\n"
               f"💰 {d.get('price')} ₸\n"
               f"📝 {d.get('desc')}\n"
               f"📸 {len(d.get('photos',[]))} фото\n"
               f"📞 {phone}\n\nВсё верно?")
    await update.message.reply_text(summary, parse_mode="Markdown",
        reply_markup=rkb(["✅ Подтвердить", "❌ Отменить"]))
    return STEP_CONFIRM

async def step_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "Подтвердить" not in update.message.text:
        await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    d = ctx.user_data
    lid = datetime.now().strftime("%Y%m%d%H%M%S")
    listing = {
        "id": lid, "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "type": d.get("type"), "rooms": d.get("rooms","—"),
        "district": d.get("district"), "address": d.get("address"),
        "area": d.get("area"), "floor": d.get("floor"),
        "price": d.get("price"), "description": d.get("desc"),
        "photos": d.get("photos",[]),
        "contact_phone": d.get("phone"),
        "contact_name": d.get("name"),
        "tg_username": d.get("username"),
        "tg_id": update.message.from_user.id,
        "crm_status": "лид", "funnel_stage": "лид",
        "notes": "", "source": "manual",
    }
    add_listing(listing)

    # Уведомление админу
    notify = (f"🔔 *Новый лид!*\n\n"
              f"🏠 {listing['type']} {listing['rooms']} комн.\n"
              f"📍 {listing['district']}, {listing['address']}\n"
              f"📐 {listing['area']} м² | 🏢 {listing['floor']}\n"
              f"💰 {listing['price']} ₸\n"
              f"📝 {listing['description']}\n"
              f"📸 {len(listing['photos'])} фото\n"
              f"👤 {listing['contact_name']} | {listing['contact_phone']}\n"
              f"✈️ @{listing['tg_username']}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Контакт",  callback_data=f"funnel_{lid}_контакт"),
         InlineKeyboardButton("👁 Показ",    callback_data=f"funnel_{lid}_показ")],
        [InlineKeyboardButton("✅ Сделка",   callback_data=f"funnel_{lid}_сделка"),
         InlineKeyboardButton("❌ Отказ",    callback_data=f"funnel_{lid}_отказ")],
        [InlineKeyboardButton("✍️ Контент",  callback_data=f"content_{lid}")],
    ])
    try:
        await ctx.bot.send_message(ADMIN_ID, notify, parse_mode="Markdown", reply_markup=kb)
        for m in listing["photos"]:
            if m["type"] == "photo": await ctx.bot.send_photo(ADMIN_ID, m["file_id"])
            else: await ctx.bot.send_video(ADMIN_ID, m["file_id"])
    except Exception as e: log.error(e)

    await update.message.reply_text(
        "✅ *Объект добавлен!*\n\nМы свяжемся с вами в ближайшее время 🤝",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ══════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════
def main():
    init_db()

    async def on_startup(application):
        scheduler = BackgroundScheduler(timezone="Asia/Almaty")
        scheduler.add_job(run_parsers, "interval", hours=12, args=[application.bot], id="parser")
        scheduler.add_job(check_followups, "interval", hours=12, args=[application.bot], id="followup")
        scheduler.start()
        await start_web()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )
    
    log.info("⏰ Парсинг каждые 12 часов| Follow-up каждые 12 часов")

    # Диалог подачи объявления
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client),
        ],
        states={
            STEP_TYPE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_type)],
            STEP_ROOMS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_rooms)],
            STEP_DISTRICT:[MessageHandler(filters.TEXT & ~filters.COMMAND, step_district)],
            STEP_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_address)],
            STEP_AREA:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_area)],
            STEP_FLOOR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_floor)],
            STEP_PRICE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_price)],
            STEP_DESC:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_desc)],
            STEP_PHOTOS:  [MessageHandler(filters.ALL, step_photos)],
            STEP_CONTACT: [MessageHandler(filters.ALL, step_contact)],
            STEP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("crm",   cmd_crm))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
        handle_note))

    print("✅ MiK Real Estate Bot запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

