"""Arranque de la ventana."""

from __future__ import annotations

import sys

MISSING_GTK = """Faltan las librerías de la ventana (GTK 4 y libadwaita). Instálalas con:

    sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1
"""


def run() -> int:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio
    except (ImportError, ValueError):
        print(MISSING_GTK, file=sys.stderr)
        return 1

    import logging

    logging.getLogger("pymobiledevice3").setLevel(logging.ERROR)

    from .. import APP_ID
    from ..core.runner import AsyncRunner
    from .window import MainWindow

    from gi.repository import GLib, Gtk

    # Para que el panel de Cinnamon asocie la ventana con su lanzador e icono
    GLib.set_prgname(APP_ID)
    Gtk.Window.set_default_icon_name(APP_ID)

    runner = AsyncRunner()
    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def on_activate(app: Adw.Application) -> None:
        window = app.get_active_window() or MainWindow(app, runner)
        window.present()

    app.connect("activate", on_activate)
    try:
        return app.run([sys.argv[0]])
    finally:
        runner.stop()
