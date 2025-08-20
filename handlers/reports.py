"""Report handlers."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

router = Router()


@router.message(F.text == "\U0001F4CA Отчёты")
async def reports_menu(message: Message) -> None:
    """Placeholder for reports menu."""
    await message.answer("Отчёты в разработке.")
