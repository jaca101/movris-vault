"""Corriente y suspensión: si el portátil se duerme a mitad, la copia se cuelga sin avisar."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

POWER_SUPPLY = Path("/sys/class/power_supply")


def on_battery(power_supply: Path = POWER_SUPPLY) -> bool:
    """True si hay batería y ningún cargador conectado. Un sobremesa sin batería nunca está «con batería»."""
    has_battery = False
    for supply in power_supply.glob("*"):
        try:
            kind = (supply / "type").read_text().strip()
            if kind == "Battery":
                has_battery = True
            elif kind in ("Mains", "USB") and (supply / "online").read_text().strip() == "1":
                return False
        except OSError:
            continue
    return has_battery


@contextmanager
def session_inhibit(reason: str) -> Iterator[Optional[int]]:
    """Pide al escritorio que no suspenda mientras dure el bloque (para la línea de órdenes).

    La ventana usa Gtk.Application.inhibit, que hace lo mismo. Si no hay sesión de escritorio,
    no bloquea nada y devuelve None.
    """
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        suspend_and_idle = 4 | 8
        (cookie,) = bus.call_sync(
            "org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager", "Inhibit",
            GLib.Variant("(susu)", ("movris-vault", 0, reason, suspend_and_idle)),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None,
        ).unpack()
    except Exception:
        yield None
        return
    try:
        yield cookie
    finally:
        bus.call_sync(
            "org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager", "Uninhibit",
            GLib.Variant("(u)", (cookie,)), None, Gio.DBusCallFlags.NONE, -1, None,
        )
