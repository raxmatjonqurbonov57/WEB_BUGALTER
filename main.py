import asyncio
import calendar
import html
import logging
import os
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ====== SOZLAMALAR ======
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PORT = int(os.getenv("PORT", "10000"))  # Render shu portni beradi
DB_PATH = os.getenv("DB_PATH", "hisob.db")  # doimiy disk ulansa: /data/hisob.db
TZ = timezone(timedelta(hours=5))       # O'zbekiston vaqti (UTC+5)
REMIND_HOUR = 21                        # kunlik eslatma vaqti (soat 21:00)

dp = Dispatcher()

# Kategoriyalar: so'z shu kalitlardan biri bilan boshlansa, kategoriya aniqlanadi
CATEGORIES = {
    "🍽 Oziq-ovqat": ["non", "osh", "ovqat", "bozor", "market", "korzinka", "meva",
                      "sabzavot", "choy", "tushlik", "kechki", "nonushta", "kafe",
                      "restoran", "lavash", "burger", "pitsa", "gosht", "go'sht",
                      "sut", "shirinlik"],
    "🚕 Transport": ["taksi", "yandex", "benzin", "avtobus", "metro", "yol", "yo'l",
                     "mashina", "bolt", "propan"],
    "🏠 Uy-joy": ["svet", "elektr", "gaz", "kommunal", "internet", "ijara",
                  "kvartira", "telefon", "tarif"],
    "👕 Kiyim": ["kiyim", "krossovka", "oyoq", "ko'ylak", "shim", "kurtka"],
    "💊 Salomatlik": ["dori", "apteka", "shifokor", "klinika", "vrach"],
    "🎬 Ko'ngilochar": ["kino", "oyin", "o'yin", "konsert", "sayohat"],
    "📚 Ta'lim": ["kurs", "kitob", "dars", "oqish", "o'qish"],
}
DEFAULT_CATEGORY = "📦 Boshqa"
INCOME_CATEGORY = "💰 Daromad"


# ====== YORDAMCHI FUNKSIYALAR ======
def now_local() -> datetime:
    return datetime.now(TZ)


def now_str() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def day_start(days_back: int = 0) -> str:
    return (now_local() - timedelta(days=days_back)).strftime("%Y-%m-%d 00:00:00")


def month_start() -> str:
    return now_local().strftime("%Y-%m-01 00:00:00")


def money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " so'm"


def bar(pct: int) -> str:
    filled = max(0, min(10, pct // 10))
    return "▓" * filled + "░" * (10 - filled)


def parse_amount(token: str):
    """'20000', '20k', '20ming' -> 20000. Tushunilmasa None."""
    t = token.lower().replace(",", "").replace("_", "")
    mult = 1
    if t.endswith("ming"):
        mult, t = 1000, t[:-4]
    elif t.endswith("k"):
        mult, t = 1000, t[:-1]
    if t.isdigit():
        return int(t) * mult
    return None


def parse_entry(text: str):
    """'taksi 20000' -> ('exp', 20000, 'taksi'); '+500000 maosh' -> ('inc', 500000, 'maosh')"""
    text = text.strip()
    kind = "exp"
    if text.startswith("+"):
        kind, text = "inc", text[1:].strip()
    parts = text.split()
    if not parts:
        return None

    amount, note_parts = parse_amount(parts[-1]), parts[:-1]
    if amount is None:
        amount, note_parts = parse_amount(parts[0]), parts[1:]
    if not amount:
        return None

    default_note = "Daromad" if kind == "inc" else "Nomsiz"
    return kind, amount, (" ".join(note_parts) or default_note)


def detect_category(note: str) -> str:
    words = note.lower().split()
    for cat, keys in CATEGORIES.items():
        if any(w.startswith(k) for w in words for k in keys):
            return cat
    return DEFAULT_CATEGORY


# ====== BAZA ======
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                budget INTEGER DEFAULT 0,
                remind INTEGER DEFAULT 1
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS tx (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,
                amount INTEGER,
                note TEXT,
                category TEXT,
                created_at TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS goals (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                target INTEGER,
                saved INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                amount INTEGER,
                created_at TEXT,
                returned INTEGER DEFAULT 0,
                returned_at TEXT
            )"""
        )
        try:  # eski bazada salary ustuni bo'lmasa qo'shamiz
            await db.execute("ALTER TABLE users ADD COLUMN salary INTEGER DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
        await db.commit()


async def ensure_user(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
        await db.commit()


async def sums(uid: int, since: str) -> tuple[int, int]:
    """(daromad, xarajat) jami"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT kind, COALESCE(SUM(amount), 0) FROM tx "
            "WHERE user_id=? AND created_at>=? GROUP BY kind",
            (uid, since),
        ) as cur:
            rows = dict(await cur.fetchall())
    return rows.get("inc", 0), rows.get("exp", 0)


async def by_category(uid: int, since: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT category, SUM(amount) AS s FROM tx "
            "WHERE user_id=? AND kind='exp' AND created_at>=? "
            "GROUP BY category ORDER BY s DESC",
            (uid, since),
        ) as cur:
            return await cur.fetchall()


async def get_budget(uid: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT budget FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def budget_status(uid: int) -> str:
    budget = await get_budget(uid)
    if not budget:
        return ""
    _, spent = await sums(uid, month_start())
    pct = spent * 100 // budget
    text = f"\n\n📊 Oylik byudjet: {bar(pct)} {pct}%"
    if spent >= budget:
        text += f"\n🚨 Byudjetdan <b>{money(spent - budget)}</b> oshib ketdi!"
    else:
        text += f"\nQoldi: {money(budget - spent)}"
        if pct >= 80:
            text += "\n⚠️ Byudjetning 80% dan ko'pi ishlatildi, ehtiyot bo'ling!"
    return text


async def goal_text(uid: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name, target, saved FROM goals WHERE user_id=?", (uid,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return "Hali maqsad yo'q.\nQo'yish: <code>/maqsad telefon 5000000</code>"
    name, target, saved = row
    pct = min(100, saved * 100 // target)
    text = (
        f"🎯 <b>Maqsad: {html.escape(name)}</b>\n"
        f"{bar(pct)} {pct}%\n"
        f"Jamg'arildi: {money(saved)} / {money(target)}"
    )
    if saved >= target:
        text += "\n\n🎉 Tabriklayman, maqsadga erishdingiz!"
    else:
        text += f"\nQolgan: {money(target - saved)}"
    return text


# ====== MAOSH, QARZLAR VA KUNLIK REJA ======
async def get_salary(uid: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT salary FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
    return (row[0] or 0) if row else 0


async def _scalar(query: str, params: tuple) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cur:
            (value,) = await cur.fetchone()
    return value or 0


async def plan(uid: int):
    """Oylik maoshdan berilgan qarzlarni ayirib, kunlik limitni hisoblaydi."""
    salary = await get_salary(uid)
    if not salary:
        return None

    now = now_local()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_left = days_in_month - now.day + 1  # bugun ham hisobga olinadi
    start = month_start()

    # shu oy berilgan va hali qaytmagan qarzlar (maoshdan ayiriladi)
    debts = await _scalar(
        "SELECT COALESCE(SUM(amount), 0) FROM debts "
        "WHERE user_id=? AND returned=0 AND created_at>=?",
        (uid, start),
    )
    # oldingi oylarda berilib, shu oy qaytgan qarzlar (shu oy mablag'iga qo'shiladi)
    returned = await _scalar(
        "SELECT COALESCE(SUM(amount), 0) FROM debts "
        "WHERE user_id=? AND returned=1 AND returned_at>=? AND created_at<?",
        (uid, start, start),
    )
    # jami qaytishi kutilayotgan qarzlar (faqat ma'lumot uchun)
    outstanding = await _scalar(
        "SELECT COALESCE(SUM(amount), 0) FROM debts WHERE user_id=? AND returned=0",
        (uid,),
    )

    _, exp_month = await sums(uid, start)
    _, exp_today = await sums(uid, day_start(0))

    available = salary - debts + returned      # shu oy sarflash mumkin bo'lgan summa
    remaining = available - exp_month          # hozirgi qoldiq
    before_today = remaining + exp_today       # bugun boshlanishidagi qoldiq
    today_limit = max(before_today, 0) // days_left

    return {
        "salary": salary,
        "debts": debts,
        "returned": returned,
        "outstanding": outstanding,
        "available": available,
        "exp_month": exp_month,
        "exp_today": exp_today,
        "remaining": remaining,
        "days_left": days_left,
        "days_in_month": days_in_month,
        "today_limit": today_limit,
    }


def today_line(p: dict) -> str:
    if p["remaining"] < 0:
        return f"🚨 Oylik mablag' tugadi ({money(-p['remaining'])} ortiqcha sarflangan)"
    if p["remaining"] == 0:
        return "🚨 Oylik mablag' tugadi"
    left = p["today_limit"] - p["exp_today"]
    text = (
        f"🎯 Bugungi limit: <b>{money(p['today_limit'])}</b>\n"
        f"Bugun sarflandi: {money(p['exp_today'])}"
    )
    if left >= 0:
        text += f"\n✅ Bugun qoldi: {money(left)}"
    else:
        text += f"\n⚠️ Bugungi limitdan {money(-left)} oshdi"
    return text


async def daily_status(uid: int) -> str:
    p = await plan(uid)
    return ("\n\n" + today_line(p)) if p else ""


async def plan_text(uid: int) -> str:
    p = await plan(uid)
    if not p:
        return "Avval oylik maoshni kiriting: <code>/maosh 5000000</code>"
    lines = [
        "📊 <b>Oylik reja</b>",
        "",
        f"💵 Maosh: {money(p['salary'])}",
        f"🤝 Shu oy berilgan qarzlar: −{money(p['debts'])}",
    ]
    if p["returned"]:
        lines.append(f"↩️ Oldingi oylardan qaytgan qarz: +{money(p['returned'])}")
    lines += [
        "",
        f"💰 Sarflash uchun: <b>{money(p['available'])}</b>",
        f"📅 Kunlik reja (butun oy): {money(max(p['available'], 0) // p['days_in_month'])}",
        f"💸 Shu oy sarflandi: {money(p['exp_month'])}",
        f"🟰 Qoldi: <b>{money(p['remaining'])}</b>",
        f"📆 Oyda {p['days_left']} kun qoldi",
        "",
        today_line(p),
    ]
    if p["outstanding"]:
        lines.append(f"\n⏳ Qaytishi kutilayotgan qarzlar: {money(p['outstanding'])}")
    return "\n".join(lines)


async def report(uid: int, since: str, title: str) -> str:
    inc, exp = await sums(uid, since)
    if not inc and not exp:
        return f"📅 <b>{title}</b>\n\nHali yozuv yo'q."
    lines = [
        f"📅 <b>{title}</b>",
        "",
        f"💰 Daromad: {money(inc)}",
        f"💸 Xarajat: {money(exp)}",
        f"🟰 Qoldi: {money(inc - exp)}",
    ]
    cats = await by_category(uid, since)
    if cats and exp:
        lines += ["", "<b>Xarajatlar bo'yicha:</b>"]
        for cat, s in cats:
            lines.append(f"{cat}: {money(s)} ({s * 100 // exp}%)")
    return "\n".join(lines)


# ====== BUYRUQLAR ======
HELP = (
    "💼 <b>Hisob-kitob yordamchisi</b>\n\n"
    "<b>Xarajat yozish:</b>\n"
    "<code>taksi 20000</code>\n"
    "<code>non 5k</code> (k = ming)\n"
    "Bir nechta xarajatni har birini yangi qatordan yozing.\n\n"
    "<b>Oylik reja:</b>\n"
    "/maosh 5000000 — oylik maoshni kiritish\n"
    "/hisob — maoshdan qarz va xarajatlar ayirilgan qoldiq, kunlik limit\n\n"
    "<b>Berilgan qarzlar:</b>\n"
    "/qarz Ali 500000 — qarz berdim\n"
    "/qarzlar — qaytmagan qarzlar\n"
    "/qaytdi ID — qarz qaytdi\n"
    "/qarzochir ID — xato yozilgan qarzni o'chirish\n\n"
    "<b>Hisobotlar:</b>\n"
    "/bugun, /hafta, /oy, /royxat, /ochir ID\n"
    "Qo'shimcha daromad: <code>+200000 premiya</code>\n\n"
    "<b>Jamg'arish:</b>\n"
    "/byudjet 3000000 — oylik xarajat limiti\n"
    "/maqsad telefon 5000000 — jamg'arma maqsadi\n"
    "/jamgarma 200000 — maqsadga pul qo'shish\n"
    "/eslatma — kunlik eslatmani yoqish/o'chirish"
)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await ensure_user(message.from_user.id)
    await message.answer(f"Salom, <b>{html.escape(message.from_user.full_name)}</b>! 👋\n\n{HELP}")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP)


@dp.message(Command("bugun"))
async def cmd_today(message: Message):
    await message.answer(await report(message.from_user.id, day_start(0), "Bugun"))


@dp.message(Command("hafta"))
async def cmd_week(message: Message):
    await message.answer(await report(message.from_user.id, day_start(6), "Oxirgi 7 kun"))


@dp.message(Command("oy"))
async def cmd_month(message: Message):
    uid = message.from_user.id
    text = await report(uid, month_start(), "Shu oy")
    await message.answer(text + await budget_status(uid))


@dp.message(Command("royxat"))
async def cmd_list(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, kind, amount, note, created_at FROM tx "
            "WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (message.from_user.id,),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        return await message.answer("Hali yozuv yo'q.")
    lines = ["🧾 <b>Oxirgi yozuvlar</b>", ""]
    for tx_id, kind, amount, note, created in rows:
        sign = "➕" if kind == "inc" else "➖"
        lines.append(
            f"<code>{tx_id}</code> {sign} {html.escape(note)} — {money(amount)} "
            f"<i>({created[5:16]})</i>"
        )
    lines.append("\nO'chirish: <code>/ochir ID</code>")
    await message.answer("\n".join(lines))


@dp.message(Command("ochir"))
async def cmd_delete(message: Message, command: CommandObject):
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Foydalanish: <code>/ochir 12</code> (ID ni /royxat dan oling)")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM tx WHERE id=? AND user_id=?",
            (int(command.args.strip()), message.from_user.id),
        )
        await db.commit()
    await message.answer("🗑 O'chirildi" if cur.rowcount else "Bunday yozuv topilmadi")


@dp.message(Command("byudjet"))
async def cmd_budget(message: Message, command: CommandObject):
    uid = message.from_user.id
    await ensure_user(uid)
    if not command.args:
        budget = await get_budget(uid)
        if not budget:
            return await message.answer("Byudjet qo'yilmagan.\nMasalan: <code>/byudjet 3000000</code>")
        return await message.answer(f"Oylik byudjet: {money(budget)}" + await budget_status(uid))

    amount = parse_amount(command.args.strip())
    if not amount:
        return await message.answer("Summani raqam bilan yozing: <code>/byudjet 3000000</code>")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET budget=? WHERE user_id=?", (amount, uid))
        await db.commit()
    await message.answer(f"✅ Oylik byudjet: {money(amount)}" + await budget_status(uid))


@dp.message(Command("maqsad"))
async def cmd_goal(message: Message, command: CommandObject):
    uid = message.from_user.id
    if not command.args:
        return await message.answer(await goal_text(uid))

    parts = command.args.split()
    target = parse_amount(parts[-1]) if len(parts) >= 2 else None
    if not target:
        return await message.answer("Foydalanish: <code>/maqsad telefon 5000000</code>")
    name = " ".join(parts[:-1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO goals (user_id, name, target, saved) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, target=excluded.target",
            (uid, name, target),
        )
        await db.commit()
    await message.answer("✅ Maqsad saqlandi!\n\n" + await goal_text(uid))


@dp.message(Command("jamgarma"))
async def cmd_save(message: Message, command: CommandObject):
    uid = message.from_user.id
    amount = parse_amount(command.args.strip()) if command.args else None
    if not amount:
        return await message.answer("Foydalanish: <code>/jamgarma 200000</code>")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE goals SET saved = saved + ? WHERE user_id=?", (amount, uid)
        )
        await db.commit()
    if not cur.rowcount:
        return await message.answer("Avval maqsad qo'ying: <code>/maqsad telefon 5000000</code>")
    await message.answer(f"💪 {money(amount)} jamg'armaga qo'shildi!\n\n" + await goal_text(uid))


@dp.message(Command("eslatma"))
async def cmd_remind(message: Message):
    uid = message.from_user.id
    await ensure_user(uid)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET remind = 1 - remind WHERE user_id=?", (uid,))
        await db.commit()
        async with db.execute("SELECT remind FROM users WHERE user_id=?", (uid,)) as cur:
            (on,) = await cur.fetchone()
    await message.answer(
        f"🔔 Kunlik eslatma yoqildi (har kuni soat {REMIND_HOUR}:00)" if on
        else "🔕 Kunlik eslatma o'chirildi"
    )


# ====== MAOSH VA QARZ BUYRUQLARI ======
@dp.message(Command("maosh"))
async def cmd_salary(message: Message, command: CommandObject):
    uid = message.from_user.id
    await ensure_user(uid)
    if not command.args:
        if not await get_salary(uid):
            return await message.answer("Oylik maoshingizni kiriting:\n<code>/maosh 5000000</code>")
        return await message.answer(await plan_text(uid))

    amount = parse_amount(command.args.strip())
    if not amount:
        return await message.answer("Summani raqam bilan yozing: <code>/maosh 5000000</code>")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET salary=? WHERE user_id=?", (amount, uid))
        await db.commit()
    await message.answer(f"✅ Oylik maosh: {money(amount)}\n\n" + await plan_text(uid))


@dp.message(Command("hisob"))
async def cmd_plan(message: Message):
    await message.answer(await plan_text(message.from_user.id))


@dp.message(Command("qarz"))
async def cmd_debt(message: Message, command: CommandObject):
    uid = message.from_user.id
    await ensure_user(uid)
    parts = command.args.split() if command.args else []
    amount = parse_amount(parts[-1]) if len(parts) >= 2 else None
    if not amount:
        return await message.answer("Foydalanish: <code>/qarz Ali 500000</code>")
    name = " ".join(parts[:-1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO debts (user_id, name, amount, created_at) VALUES (?, ?, ?, ?)",
            (uid, name, amount, now_str()),
        )
        await db.commit()
    await message.answer(
        f"🤝 Qarz yozildi: <b>{html.escape(name)}</b> — {money(amount)}\n\n" + await plan_text(uid)
    )


@dp.message(Command("qarzlar"))
async def cmd_debts(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, amount, created_at FROM debts "
            "WHERE user_id=? AND returned=0 ORDER BY id DESC",
            (message.from_user.id,),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        return await message.answer("Qaytmagan qarz yo'q ✅")
    total = sum(r[2] for r in rows)
    lines = ["🤝 <b>Berilgan qarzlar</b>", ""]
    for debt_id, name, amount, created in rows:
        lines.append(f"<code>{debt_id}</code> {html.escape(name)} — {money(amount)} <i>({created[:10]})</i>")
    lines += [
        "",
        f"Jami: <b>{money(total)}</b>",
        "Qaytganda: <code>/qaytdi ID</code>",
        "Xato yozilgan bo'lsa: <code>/qarzochir ID</code>",
    ]
    await message.answer("\n".join(lines))


@dp.message(Command("qaytdi"))
async def cmd_returned(message: Message, command: CommandObject):
    uid = message.from_user.id
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Foydalanish: <code>/qaytdi 3</code> (ID ni /qarzlar dan oling)")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE debts SET returned=1, returned_at=? WHERE id=? AND user_id=? AND returned=0",
            (now_str(), int(command.args.strip()), uid),
        )
        await db.commit()
    if not cur.rowcount:
        return await message.answer("Bunday qaytmagan qarz topilmadi")
    await message.answer("✅ Qarz qaytdi deb belgilandi\n\n" + await plan_text(uid))


@dp.message(Command("qarzochir"))
async def cmd_debt_delete(message: Message, command: CommandObject):
    uid = message.from_user.id
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Foydalanish: <code>/qarzochir 3</code>")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM debts WHERE id=? AND user_id=?", (int(command.args.strip()), uid)
        )
        await db.commit()
    await message.answer("🗑 O'chirildi" if cur.rowcount else "Bunday qarz topilmadi")


# ====== XARAJAT / DAROMAD YOZISH ======
MAX_PER_MESSAGE = 8  # bir xabarda ko'pi bilan nechta qator qabul qilinadi


@dp.message(F.text, ~F.text.startswith("/"))
async def add_entry(message: Message):
    # Har qator alohida yozuv. Summa topilmagan qatorlar (masalan "bugun") o'tkazib yuboriladi.
    entries = [e for e in (parse_entry(line) for line in message.text.splitlines()) if e]
    entries = entries[:MAX_PER_MESSAGE]
    if not entries:
        return await message.answer(
            "Tushunmadim 🤔 Mana shunday yozing:\n"
            "<code>taksi 20000</code>\n"
            "Daromad uchun: <code>+500000 maosh</code>\n\n"
            "Bir nechta yozuvni har birini yangi qatordan yozishingiz mumkin."
        )

    uid = message.from_user.id
    await ensure_user(uid)

    lines, ids = [], []
    async with aiosqlite.connect(DB_PATH) as db:
        for kind, amount, note in entries:
            category = detect_category(note) if kind == "exp" else INCOME_CATEGORY
            cur = await db.execute(
                "INSERT INTO tx (user_id, kind, amount, note, category, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, kind, amount, note, category, now_str()),
            )
            ids.append(str(cur.lastrowid))
            sign = "➕" if kind == "inc" else "➖"
            lines.append(f"{sign} {html.escape(note)} — {money(amount)} ({category})")
        await db.commit()

    _, today_exp = await sums(uid, day_start(0))
    text = (
        "✅ <b>Saqlandi:</b>\n"
        + "\n".join(lines)
        + f"\n\n📅 Bugun jami xarajat: {money(today_exp)}"
        + await daily_status(uid)
        + await budget_status(uid)
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Bekor qilish", callback_data="undo:" + ",".join(ids))]]
    )
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("undo:"))
async def undo(call: CallbackQuery):
    ids = [int(x) for x in call.data.split(":")[1].split(",") if x.isdigit()]
    deleted = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for tx_id in ids:
            cur = await db.execute(
                "DELETE FROM tx WHERE id=? AND user_id=?", (tx_id, call.from_user.id)
            )
            deleted += cur.rowcount
        await db.commit()
    await call.message.edit_text("↩️ Bekor qilindi" if deleted else "Allaqachon o'chirilgan")
    await call.answer()


# ====== KUNLIK ESLATMA ======
async def reminder_loop(bot: Bot):
    last_sent = None
    while True:
        now = now_local()
        if now.hour == REMIND_HOUR and last_sent != now.date():
            last_sent = now.date()
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT user_id FROM users WHERE remind=1 AND user_id NOT IN "
                    "(SELECT user_id FROM tx WHERE created_at>=?)",
                    (day_start(0),),
                ) as cur:
                    ids = [r[0] for r in await cur.fetchall()]
            for uid in ids:
                try:
                    await bot.send_message(
                        uid,
                        "🔔 Bugun xarajatlaringizni yozdingizmi?\n"
                        "Masalan: <code>tushlik 30000</code>",
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.05)
        await asyncio.sleep(30)


# ====== RENDER UCHUN VEB-SERVER ======
async def start_web_server():
    async def health(_):
        return web.Response(text="Bot ishlayapti")

    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()


# ====== ISHGA TUSHIRISH ======
async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi! Muhit o'zgaruvchisini o'rnating.")

    await init_db()
    await start_web_server()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    await bot.set_my_commands([
        BotCommand(command="hisob", description="Oylik reja va kunlik limit"),
        BotCommand(command="maosh", description="Oylik maoshni kiritish"),
        BotCommand(command="qarz", description="Qarz berdim"),
        BotCommand(command="qarzlar", description="Berilgan qarzlar"),
        BotCommand(command="bugun", description="Bugungi xarajatlar"),
        BotCommand(command="hafta", description="Oxirgi 7 kun"),
        BotCommand(command="oy", description="Shu oy hisoboti"),
        BotCommand(command="royxat", description="Oxirgi yozuvlar"),
        BotCommand(command="byudjet", description="Oylik byudjet"),
        BotCommand(command="maqsad", description="Jamg'arma maqsadi"),
        BotCommand(command="jamgarma", description="Maqsadga pul qo'shish"),
        BotCommand(command="help", description="Yordam"),
    ])

    asyncio.create_task(reminder_loop(bot))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
