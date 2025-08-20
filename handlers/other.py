"""Miscellaneous handlers."""
from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

router = Router()


@router.message()
async def fallback(message: Message) -> None:
    """Reply to unknown messages."""
    await message.answer("Команда не распознана. Используйте меню.")
