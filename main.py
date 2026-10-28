import os
import asyncio
import aiosqlite

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = "8870357747:AAEcYCuuAZ-WyAT5adqTjcrN8bzccNyrX0U"
ADMIN_IDS = {8599541645}
DB_PATH = "bot.db"
PAGE_SIZE = 5

router = Router()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT,
                is_blocked INTEGER DEFAULT 0
            )"""
        )
        await db.commit()


async def save_user(telegram_id: int, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (telegram_id, full_name) VALUES (?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET full_name = excluded.full_name""",
            (telegram_id, full_name),
        )
        await db.commit()


async def list_users(page: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT telegram_id, full_name, is_blocked FROM users "
            "ORDER BY full_name LIMIT ? OFFSET ?",
            (PAGE_SIZE + 1, page * PAGE_SIZE),
        )
        rows = await cursor.fetchall()
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


async def get_user(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT telegram_id, full_name, is_blocked FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
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
        cursor = await db.execute("SELECT COUNT(*), SUM(is_blocked) FROM users")
        total, blocked = await cursor.fetchone()
        return total or 0, blocked or 0


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Foydalanuvchilar", callback_data="users:0")],
        [InlineKeyboardButton(text="Statistika", callback_data="stats")],
    ])


def users_keyboard(users, page: int, has_next: bool):
    kb = []
    for telegram_id, full_name, is_blocked in users:
        status = "😡" if is_blocked else "🍏"
        kb.append([InlineKeyboardButton(
            text=f"{status} {full_name}", callback_data=f"user:{telegram_id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="👈", callback_data=f"users:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="👉", callback_data=f"users:{page + 1}"))
    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton(text="Menyu", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def user_detail_keyboard(telegram_id: int, is_blocked: bool):
    text = "Blokdan chiqarish" if is_blocked else "Bloklash"
    action = "unblock" if is_blocked else "block"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=f"{action}:{telegram_id}")],
        [InlineKeyboardButton(text="Ro'yxatga", callback_data="users:0")],
    ])
