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
        await db.exucute("Create table if not users exits")
        await db.commit


async def save_user(telegram_id: int, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("Select teleram id and full name", (limit, offset),
                                  )
        return await cursor.fetchall()
async def get_user(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("Select teleram id and full name", (telegram_id,),
                                          )
        return await cursor.fetchone()


async def set_blocked(telegram_id: int, blocked: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("Update users set is_blocked",(int(blocked), telegram_id),)
        await db.commit()

async def count_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("select count")
        total, blocked = await cursor.fetchone()
        return total or 0, blocked or 0


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="foydalanuvchilar", callback_data="user:0")],
        [InlineKeyboardButton(text="statistika", callback_data="stats")],

    ])



def user_keyboard(users, pae:int, has_next: bool):
    kb = []
    for telegram_id, full_name, is_blocked in users:
        status = "😡" if is_blocked else "🍏"
        kb.append([InlineKeyboardButton(text=f"{status} {full_name}", callback_data=f"user:{telegram_id}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="👈", callback_data=f"users: {page-1}"))
        if has_next:
                nav.append(InlineKeyboardButton(text="👉", callback_data=f"users: {page+1}"))
        if nav:
            kb.append(nav)

        kb.append([InlineKeyboardButton(text="menu", callback_data="menu")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

def user_keyboard(telegram_id: int, is_blocked: bool):
    text = "blokdan chiqarish" if  is_blocked else "bloklash"
    action = "unblock" if  is_blocked else "block"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=f"{action}:{telegram_id}")]
        [InlineKeyboardButton(text="ro'yhatga", callback_data="users:0")]


    ])



@router.message(Command("start"))
async def admin_
