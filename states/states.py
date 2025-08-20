"""Finite state machine states."""
from aiogram.fsm.state import StatesGroup, State


class DealStates(StatesGroup):
    """States used during deal creation."""

    waiting_for_ticker = State()
    waiting_for_comment = State()
