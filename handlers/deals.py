"""Deal related handlers."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

router = Router()


@router.message(F.text == "\U0001F4E6 Сделки")
async def deals_menu(message: Message) -> None:
    """Placeholder for deals menu."""
    await message.answer("Меню сделок в разработке.")
