"""Bot entry point."""
from __future__ import annotations

import asyncio
from aiogram import Bot, Dispatcher
from aiogram.utils.token import TokenValidationError

from config import BOT_TOKEN
from handlers import register_handlers


async def main() -> None:
    """Start the bot."""
    print("[main] Starting bot initialization")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in the environment or .env file.")
    try:
        bot = Bot(BOT_TOKEN)
    except TokenValidationError as exc:
        raise RuntimeError("BOT_TOKEN is invalid.") from exc
    print("[main] Bot instance created")
    dp = Dispatcher()
    print("[main] Dispatcher created; registering handlers")
    register_handlers(dp)
    print("[main] Handlers registered; starting polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
