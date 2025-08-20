"""Database helpers."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def get_connection(path: str | Path = "bot.db") -> sqlite3.Connection:
    """Return a SQLite connection to the given database path."""
    return sqlite3.connect(path)
