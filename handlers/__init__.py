"""Handlers package."""
from __future__ import annotations

from aiogram import Dispatcher

from . import start, deals, reports, sets, other


def register_handlers(dp: Dispatcher) -> None:
    """Register all routers with the dispatcher."""
    print("[handlers] register_handlers: start")
    print("[handlers] including start.router")
    dp.include_router(start.router)
    print("[handlers] including deals.router")
    dp.include_router(deals.router)
    print("[handlers] including reports.router")
    dp.include_router(reports.router)
    print("[handlers] including sets.router")
    dp.include_router(sets.router)
    print("[handlers] including other.router")
    dp.include_router(other.router)
    print("[handlers] register_handlers: done")
