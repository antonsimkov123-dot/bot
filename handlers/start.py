"""Start command and main menu handlers."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from keyboards.keyboards import main_menu

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Greet user and show main menu."""
    await message.answer("Привет! Это CryptoLensBot.", reply_markup=main_menu())
