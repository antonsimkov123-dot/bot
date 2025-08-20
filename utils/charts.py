"""Chart generation utilities."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def empty_chart(path: str) -> None:
    """Save an empty placeholder chart to ``path``."""
    fig, ax = plt.subplots()
    ax.text(0.5, 0.5, "No data", ha="center")
    fig.savefig(path)
