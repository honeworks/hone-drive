"""Numbers for people: sizes, speeds and times in the units the dashboard and the CLI show."""

from __future__ import annotations


def size(n: float | None) -> str:
    """Decimal units, as drives are sold and as `df -H` shows them: 388 GB, 1.2 TB."""
    if n is None:
        return "?"
    for unit, scale in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if abs(n) >= scale:
            value = n / scale
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
    return f"{int(n)} B"


def speed(gbs: float | None) -> str:
    if gbs is None:
        return "?"
    return f"{gbs:.2f} GB/s" if gbs >= 1 else f"{gbs * 1000:.0f} MB/s"


def seconds(s: float | None) -> str:
    if s is None:
        return "?"
    if s < 60:
        return f"{s:.1f} s" if s < 10 else f"{s:.0f} s"
    if s < 3600:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


def percent(fraction: float) -> str:
    return f"{fraction * 100:.0f}%"
