
import os
import asyncio
import aiosqlite

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiohttp import web

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {8599541645}
DB_PATH = "bot.db"
PAGE_SIZE = 5

router = Router()


# DATABASE
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT,
                is_blocked INTEGER DEFAULT 0
            )
        """)
        await db.commit()


async def save_user(telegram_id: int, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, full_name)
            VALUES (?, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET full_name = excluded.full_name
        """, (telegram_id, full_name))
        await db.commit()


async def list_users(page: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT telegram_id, full_name, is_blocked
            FROM users
            ORDER BY full_name
            LIMIT ? OFFSET ?
        """, (PAGE_SIZE + 1, page * PAGE_SIZE))
        rows = await cursor.fetchall()

    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


async def get_user(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT telegram_id, full_name, is_blocked
            FROM users WHERE telegram_id = ?
        """, (telegram_id,))
        return await cursor.fetchone()


async def set_blocked(telegram_id: int, blocked: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_blocked = ? WHERE telegram_id = ?",
            (int(blocked), telegram_id),
        )
        await db.commit()


async def count_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*), SUM(is_blocked) FROM users"
        )
        total, blocked = await cursor.fetchone()

    return total or 0, blocked or 0


# KEYBOARDS
def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Foydalanuvchilar",
            callback_data="users:0"
        )],
        [InlineKeyboardButton(
            text="Statistika",
            callback_data="stats"
        )],
    ])


def users_keyboard(users, page: int, has_next: bool):
    kb = []

    for telegram_id, full_name, is_blocked in users:
        status = "😡" if is_blocked else "🍏"
        kb.append([InlineKeyboardButton(
            text=f"{status} {full_name or telegram_id}",
            callback_data=f"user:{telegram_id}",
        )])

    nav = []

    if page > 0:
        nav.append(InlineKeyboardButton(
            text="👈", callback_data=f"users:{page - 1}"
        ))

    if has_next:
        nav.append(InlineKeyboardButton(
            text="👉", callback_data=f"users:{page + 1}"
        ))

    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton(
        text="Menyu", callback_data="menu"
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def user_detail_keyboard(telegram_id: int, is_blocked: bool):
    text = "Blokdan chiqarish" if is_blocked else "Bloklash"
    action = "unblock" if is_blocked else "block"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=text, callback_data=f"{action}:{telegram_id}"
        )],
        [InlineKeyboardButton(
            text="Ro'yxatga", callback_data="users:0"
        )],
    ])


# BOT HANDLERS
@router.message(Command("start"))
async def start_handler(message: Message):
    user = message.from_user

    if not user:
        return

    await save_user(user.id, user.full_name)

    if user.id in ADMIN_IDS:
        await message.answer(
            "Admin paneliga xush kelibsiz!",
            reply_markup=admin_menu(),
        )
    else:
        await message.answer("Salom! Botga xush kelibsiz.")


@router.message(Command("admin"))
async def admin_handler(message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Sizda ruxsat yo'q.")
        return

    await message.answer("Admin paneli:", reply_markup=admin_menu())


@router.callback_query()
async def callback_handler(callback: CallbackQuery):
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    data = callback.data or ""

    if data == "menu":
        if callback.message:
            await callback.message.edit_text(
                "Admin paneli:", reply_markup=admin_menu()
            )
        await callback.answer()
        return

    if data == "stats":
        total, blocked = await count_users()

        if callback.message:
            await callback.message.edit_text(
                f"📊 Statistika\n\n"
                f"Jami foydalanuvchilar: {total}\n"
                f"Bloklanganlar: {blocked}",
                reply_markup=admin_menu(),
            )

        await callback.answer()
        return

    if data.startswith("users:"):
        page = max(0, int(data.split(":")[1]))
        users, has_next = await list_users(page)

        if callback.message:
            await callback.message.edit_text(
                f"Foydalanuvchilar (sahifa {page + 1}):",
                reply_markup=users_keyboard(users, page, has_next),
            )

        await callback.answer()
        return

    if data.startswith("user:"):
        telegram_id = int(data.split(":")[1])
        user = await get_user(telegram_id)

        if not user:
            await callback.answer(
                "Foydalanuvchi topilmadi.", show_alert=True
            )
            return

        uid, full_name, is_blocked = user

        if callback.message:
            await callback.message.edit_text(
                f"👤 {full_name or 'Nomsiz'}\n"
                f"ID: {uid}\n"
                f"Holati: {'Bloklangan' if is_blocked else 'Faol'}",
                reply_markup=user_detail_keyboard(uid, bool(is_blocked)),
            )

        await callback.answer()
        return

    if data.startswith("block:") or data.startswith("unblock:"):
        action, user_id = data.split(":", 1)
        telegram_id = int(user_id)

        if telegram_id in ADMIN_IDS:
            await callback.answer(
                "Adminni bloklab bo'lmaydi.", show_alert=True
            )
            return

        user = await get_user(telegram_id)

        if not user:
            await callback.answer(
                "Foydalanuvchi topilmadi.", show_alert=True
            )
            return

        blocked = action == "block"
        await set_blocked(telegram_id, blocked)

        await callback.answer(
            "Bloklandi." if blocked else "Blokdan chiqarildi."
        )

        if callback.message:
            await callback.message.edit_text(
                f"👤 {user[1] or 'Nomsiz'}\n"
                f"ID: {telegram_id}\n"
                f"Holati: {'Bloklangan' if blocked else 'Faol'}",
                reply_markup=user_detail_keyboard(
                    telegram_id, blocked
                ),
            )


# RENDER WEB SERVER
async def health(request):
    return web.Response(text="ok")


async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"Web server ishga tushdi: port {port}")
    return runner


# MAIN
async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. Render Environment sozlamasini tekshiring."
        )

    await init_db()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    runner = await start_web()

    try:
        print("Telegram bot ishga tushdi!")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
