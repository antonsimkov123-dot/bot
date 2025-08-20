"""Bot keyboards."""
from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu() -> ReplyKeyboardMarkup:
    """Return the main menu keyboard."""
    keyboard = [
        [KeyboardButton(text="\U0001F4E6 Сделки"), KeyboardButton(text="\U0001F4CA Отчёты")],
        [KeyboardButton(text="\U0001F514 Напоминания"), KeyboardButton(text="\U0001F9F9 Очистить всё")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
