"""Números y fechas para la pantalla, en el idioma elegido."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .i18n import _, current_language, ngettext


def number(value: float, decimals: int = 1) -> str:
    text = f"{value:.{decimals}f}"
    # Coma decimal en español; punto en inglés
    return text.replace(".", ",") if current_language() == "es" else text


def size(n: Optional[float]) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000:
            return f"{n:.0f} {unit}" if unit == "B" else f"{number(n)} {unit}"
        n /= 1000
    return f"{number(n)} TB"


def percent(value: float, decimals: int = 0) -> str:
    return f"{number(value, decimals)} %" if current_language() == "es" else f"{number(value, decimals)}%"


def duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return _("calculating…")
    minutes = round(seconds / 60)
    if minutes < 1:
        return _("less than a minute")
    if minutes < 60:
        return _("{minutes} min").format(minutes=minutes)
    return _("{hours} h {minutes:02d} min").format(hours=minutes // 60, minutes=minutes % 60)


def ago(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    if when is None:
        return _("never")
    now = now or datetime.now(timezone.utc)
    days = (now.date() - when.astimezone(now.tzinfo).date()).days
    if days <= 0:
        return _("today at {time}").format(time=when.astimezone().strftime("%H:%M"))
    if days == 1:
        return _("yesterday")
    if days < 30:
        return ngettext("{n} day ago", "{n} days ago", days).format(n=days)
    months = days // 30
    return ngettext("{n} month ago", "{n} months ago", months).format(n=months)
