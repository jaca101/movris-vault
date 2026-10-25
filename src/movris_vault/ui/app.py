"""Arranque de la ventana, el menú principal y el cambio de idioma."""

from __future__ import annotations

import sys

from ..i18n import _


def run() -> int:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib, Gtk
    except (ImportError, ValueError):
        print(_("The window libraries (GTK 4 and libadwaita) are missing. Install them with:"), file=sys.stderr)
        print("\n    sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1\n", file=sys.stderr)
        return 1

    import logging

    logging.getLogger("pymobiledevice3").setLevel(logging.ERROR)

    from .. import APP_ID, APP_NAME, WEBSITE, __version__, i18n
    from ..core import settings
    from ..core.runner import AsyncRunner
    from .window import MainWindow

    # Para que el panel de Cinnamon asocie la ventana con su lanzador e icono
    GLib.set_prgname(APP_ID)
    Gtk.Window.set_default_icon_name(APP_ID)

    runner = AsyncRunner()
    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def on_activate(app: Adw.Application) -> None:
        window = app.get_active_window() or MainWindow(app, runner)
        window.present()

    def on_language(action: Gio.SimpleAction, value: GLib.Variant) -> None:
        window = app.get_active_window()
        if isinstance(window, MainWindow) and not window.can_rebuild():
            return  # a mitad de una copia no se rehace la ventana
        choice = value.get_string()
        action.set_state(value)
        prefs = settings.load()
        prefs.language = choice
        settings.save(prefs)
        i18n.set_language(choice)
        # Los textos se ponen al construir la ventana: la forma más simple de cambiarlos es hacer otra
        new_window = MainWindow(app, runner)
        new_window.present()
        if window is not None:
            window.destroy()

    def on_about(*_args) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            comments=_("Encrypted iPhone backups for Linux."),
            website=WEBSITE,
            issue_url=f"{WEBSITE}/issues",
            license_type=Gtk.License.GPL_3_0,
            developer_name="jaca101",
        )
        about.present(app.get_active_window())

    language = Gio.SimpleAction.new_stateful(
        "language", GLib.VariantType.new("s"), GLib.Variant("s", settings.load().language)
    )
    language.connect("activate", on_language)
    app.add_action(language)
    about = Gio.SimpleAction.new("about", None)
    about.connect("activate", on_about)
    app.add_action(about)

    app.connect("activate", on_activate)
    try:
        return app.run([sys.argv[0]])
    finally:
        runner.stop()


def main_menu() -> "Gio.Menu":
    from gi.repository import Gio

    from .. import i18n

    languages = Gio.Menu()
    languages.append(_("Automatic (system)"), f"app.language::{i18n.AUTO}")
    for code, name in i18n.LANGUAGES.items():
        languages.append(name, f"app.language::{code}")
    menu = Gio.Menu()
    menu.append_submenu(_("Language"), languages)
    menu.append(_("About Movris Vault"), "app.about")
    return menu
