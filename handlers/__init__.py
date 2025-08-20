"""Handlers package."""
from __future__ import annotations

from aiogram import Dispatcher

from . import start, deals, reports, sets, other


def register_handlers(dp: Dispatcher) -> None:
    """Register all routers with the dispatcher."""
    dp.include_router(start.router)
    dp.include_router(deals.router)
    dp.include_router(reports.router)
    dp.include_router(sets.router)
    dp.include_router(other.router)
