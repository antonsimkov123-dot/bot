"""Miscellaneous helper utilities."""
from __future__ import annotations

from datetime import datetime


def now_str() -> str:
    """Return current UTC time as ISO string."""
    return datetime.utcnow().isoformat()
