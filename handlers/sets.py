"""Setup analysis handlers."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

router = Router()


@router.message(F.text == "\U0001F4C8 Сетап-анализ")
async def setup_analysis(message: Message) -> None:
    """Placeholder for setup analysis."""
    await message.answer("Аналитика по сетапам в разработке.")
