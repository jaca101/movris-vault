"""Números y fechas en castellano para la pantalla."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def size(n: Optional[float]) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1000
    return f"{n:.1f} TB".replace(".", ",")


def duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "calculando…"
    minutes = round(seconds / 60)
    if minutes < 1:
        return "menos de un minuto"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def ago(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    if when is None:
        return "nunca"
    now = now or datetime.now(timezone.utc)
    days = (now.date() - when.astimezone(now.tzinfo).date()).days
    if days <= 0:
        return f"hoy a las {when.astimezone().strftime('%H:%M')}"
    if days == 1:
        return "ayer"
    if days < 30:
        return f"hace {days} días"
    months = days // 30
    return "hace 1 mes" if months == 1 else f"hace {months} meses"
