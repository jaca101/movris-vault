"""La ventana principal: iPhone → botón → progreso → resultado."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Callable, Optional

from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_NAME, fmt
from ..core import device, encryption, power, settings, storage
from ..core.backup import (
    BackupJob,
    Phase,
    Snapshot,
    encryption_changed,
    has_previous_backup,
    next_backup_is_incremental,
)
from ..core.device import DeviceState, DeviceStatus
from ..core.errors import friendly
from ..core.runner import AsyncRunner

POLL_SECONDS = 2
# usbmuxd tarda un momento en ver un iPhone recién enchufado: no pedir reconectar a la primera
RECONNECT_CONFIRM_POLLS = 3
PAIR_TIMEOUT_SECONDS = 300

DEVICE_PAGES = {
    DeviceState.ABSENT: ("phone-symbolic", "Conecta el iPhone", "Usa el cable USB y desbloquéalo."),
    DeviceState.RECONNECT: (
        "dialog-warning-symbolic",
        "Desconecta y vuelve a conectar el cable",
        "El iPhone está enchufado pero no responde. Pasa a veces cuando el portátil se ha dormido.",
    ),
    DeviceState.UNTRUSTED: (
        "dialog-password-symbolic",
        "Mira el iPhone",
        "Pulsa «Confiar» y escribe tu código.\nSolo hace falta la primera vez.",
    ),
}

PROGRESS_TEXT = {
    Phase.CONNECTING: ("phone-symbolic", "Preparando la copia…", "Conectando con el iPhone."),
    Phase.WAITING_PASSCODE: (
        "dialog-password-symbolic",
        "Escribe tu código en el iPhone",
        "El iPhone lo pide para empezar la copia.",
    ),
    Phase.COPYING: ("document-save-symbolic", "Copiando el iPhone…", ""),
}


def on_main(fn: Callable, *args) -> None:
    """Ejecuta fn en el hilo de la interfaz (GTK no se puede tocar desde otros hilos)."""

    def call() -> bool:
        fn(*args)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(call)


def pill_button(label: str, style: str) -> Gtk.Button:
    return Gtk.Button(label=label, halign=Gtk.Align.CENTER, css_classes=["pill", style])


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, runner: AsyncRunner) -> None:
        super().__init__(application=app, title=APP_NAME, default_width=560, default_height=720)
        self.runner = runner
        self.settings = settings.load()
        self.status: Optional[DeviceStatus] = None
        self.backups: list[storage.BackupEntry] = []
        self.job: Optional[BackupJob] = None
        self.inhibit_cookie = 0
        self.probing = False
        self.pairing = False
        self.reconnect_polls = 0
        self.showing_result = False
        self.close_after_backup = False
        self.pulse_source = 0
        self.busy = False  # cambiando la contraseña: no sondear el iPhone mientras tanto
        self.result_aside: Optional[Path] = None

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.battery_banner = Adw.Banner(title="El portátil va con batería: enchúfalo antes de hacer la copia.")
        toolbar.add_top_bar(self.battery_banner)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self.stack)
        self.set_content(toolbar)

        self._build_device_page()
        self._build_home_page()
        self._build_progress_page()
        self._build_result_page()
        self._build_working_page()
        self.stack.set_visible_child_name("device")

        self.connect("close-request", self._on_close_request)
        self._refresh_backups()
        self._poll()
        GLib.timeout_add_seconds(POLL_SECONDS, self._poll)

    # --- Construcción ---

    def _build_device_page(self) -> None:
        self.device_page = Adw.StatusPage()
        self.stack.add_named(self.device_page, "device")

    def _build_home_page(self) -> None:
        self.home_page = Adw.StatusPage(icon_name="phone-symbolic")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self.backup_button = pill_button("Hacer copia ahora", "suggested-action")
        self.backup_button.connect("clicked", self._on_backup_clicked)
        box.append(self.backup_button)
        self.last_backup_label = Gtk.Label(css_classes=["title-4"], margin_top=6)
        box.append(self.last_backup_label)
        self.backup_kind_label = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        box.append(self.backup_kind_label)

        encryption_group = Adw.PreferencesGroup(title="Cifrado", margin_top=24)
        self.encryption_row = Adw.ActionRow(title="Copia cifrada", subtitle_lines=3)
        self.encryption_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.encryption_button.connect("clicked", self._on_encryption_clicked)
        self.encryption_row.add_suffix(self.encryption_button)
        encryption_group.add(self.encryption_row)
        box.append(encryption_group)

        self.backups_group = Adw.PreferencesGroup(title="Copias guardadas")
        self.backup_rows: list[Gtk.Widget] = []
        box.append(self.backups_group)

        folder_group = Adw.PreferencesGroup(title="Dónde se guardan")
        self.folder_row = Adw.ActionRow(title="Carpeta")
        change = Gtk.Button(label="Cambiar…", valign=Gtk.Align.CENTER)
        change.connect("clicked", self._on_change_folder)
        self.folder_row.add_suffix(change)
        folder_group.add(self.folder_row)
        box.append(folder_group)

        self.home_page.set_child(Adw.Clamp(maximum_size=480, child=box))
        self.stack.add_named(self.home_page, "home")

    def _build_progress_page(self) -> None:
        self.progress_page = Adw.StatusPage()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.progress_bar = Gtk.ProgressBar()
        box.append(self.progress_bar)
        self.progress_details = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        box.append(self.progress_details)
        self.stalled_label = Gtk.Label(
            label="Parece que no avanza. Si sigue así unos minutos, cancela, desconecta el cable y vuelve a conectarlo.",
            css_classes=["warning"], wrap=True, justify=Gtk.Justification.CENTER, visible=False,
        )
        box.append(self.stalled_label)
        box.append(Gtk.Label(label="No desconectes el iPhone.", css_classes=["heading"], margin_top=12))
        self.cancel_button = pill_button("Cancelar", "destructive-action")
        self.cancel_button.set_margin_top(12)
        self.cancel_button.connect("clicked", lambda _b: self._confirm_cancel())
        box.append(self.cancel_button)
        self.progress_page.set_child(Adw.Clamp(maximum_size=420, child=box))
        self.stack.add_named(self.progress_page, "progress")

    def _build_result_page(self) -> None:
        self.result_page = Adw.StatusPage()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        back = pill_button("Volver", "suggested-action")
        back.connect("clicked", self._on_result_back)
        box.append(back)
        # Tras activar el cifrado, la copia antigua sin cifrar se aparta y se ofrece borrarla al terminar
        self.delete_aside_button = Gtk.Button(label="Borrar la copia anterior sin cifrar", halign=Gtk.Align.CENTER,
                                              css_classes=["flat", "error"], visible=False)
        self.delete_aside_button.connect("clicked", lambda _b: self._confirm_delete(self.result_aside))
        box.append(self.delete_aside_button)
        self.result_page.set_child(box)
        self.stack.add_named(self.result_page, "result")

    def _build_working_page(self) -> None:
        self.working_page = Adw.StatusPage(
            icon_name="dialog-password-symbolic",
            description="Si el iPhone te pide el código, escríbelo.",
            child=Gtk.Spinner(spinning=True, width_request=32, height_request=32),
        )
        self.stack.add_named(self.working_page, "working")

    # --- Utilidades ---

    def _submit(self, coro, on_done: Callable[[object, Optional[BaseException]], None]) -> None:
        def done(future) -> None:
            if future.cancelled():
                on_main(on_done, None, asyncio.CancelledError())
            elif future.exception() is not None:
                on_main(on_done, None, future.exception())
            else:
                on_main(on_done, future.result(), None)

        self.runner.submit(coro).add_done_callback(done)

    def _ask(self, heading: str, body: str, ok_label: str, on_ok: Callable[[], None],
             cancel_label: str = "Cancelar", destructive: bool = False) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", cancel_label)
        dialog.add_response("ok", ok_label)
        dialog.set_response_appearance(
            "ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: on_ok() if response == "ok" else None)
        dialog.present(self)

    def _tell(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "Entendido")
        dialog.present(self)

    # --- iPhone ---

    def _poll(self) -> bool:
        self.battery_banner.set_revealed(power.on_battery())
        if not (self.job or self.probing or self.pairing or self.busy):
            self.probing = True
            self._submit(device.probe(), self._on_probe)
        return GLib.SOURCE_CONTINUE

    def _on_probe(self, status: Optional[DeviceStatus], exc: Optional[BaseException]) -> None:
        self.probing = False
        if exc is not None or status is None:
            status = DeviceStatus(DeviceState.ABSENT)
        if status.state == DeviceState.RECONNECT:
            self.reconnect_polls += 1
            if self.reconnect_polls < RECONNECT_CONFIRM_POLLS:
                status = DeviceStatus(DeviceState.ABSENT)
        else:
            self.reconnect_polls = 0
        if self.job or self.busy:
            return
        self._apply_status(status)
        if status.state == DeviceState.UNTRUSTED and not self.pairing:
            self.pairing = True
            self._submit(self._pair(), self._on_paired)

    async def _pair(self) -> None:
        lockdown = await device.connect(pair=True, pair_timeout=PAIR_TIMEOUT_SECONDS)
        await lockdown.close()

    def _on_paired(self, _result, _exc: Optional[BaseException]) -> None:
        # Si falló (bloqueado, desenchufado, «No confiar»…) el siguiente sondeo vuelve a intentarlo
        self.pairing = False
        self._poll()

    def _apply_status(self, status: DeviceStatus) -> None:
        self.status = status
        if self.showing_result:
            return
        if status.info is None:
            icon, title, description = DEVICE_PAGES[status.state]
            self.device_page.set_icon_name(icon)
            self.device_page.set_title(title)
            self.device_page.set_description(description)
            self.stack.set_visible_child_name("device")
            return
        info = status.info
        self.home_page.set_title(info.name)
        self.home_page.set_description(
            f"iOS {info.ios_version} · {fmt.size(info.used)} usados de {fmt.size(info.capacity)}"
        )
        self._update_last_backup()
        self._update_encryption_row(info)
        self.stack.set_visible_child_name("home")

    # --- Cifrado ---

    def _update_encryption_row(self, info: device.DeviceInfo) -> None:
        if info.encrypted:
            self.encryption_row.set_subtitle("Activada: la copia guarda también contraseñas, Salud y WhatsApp.")
            self.encryption_button.set_label("Cambiar contraseña…")
            self.encryption_button.set_css_classes([])
        else:
            self.encryption_row.set_subtitle("Desactivada: la copia no guarda contraseñas, ni datos de Salud ni de WhatsApp.")
            self.encryption_button.set_label("Activar…")
            self.encryption_button.set_css_classes(["suggested-action"])

    def _on_encryption_clicked(self, _button) -> None:
        info = self.status.info if self.status else None
        if info is None:
            return
        if not info.encrypted:
            self._ask_new_password(info, old=None)
            return
        # Para cambiarla hace falta la actual: normalmente está en el llavero
        self._submit(asyncio.to_thread(encryption.lookup_password, info.udid),
                     lambda old, _e: self._ask_new_password(info, old=old or ""))

    def _ask_new_password(self, info: device.DeviceInfo, old: Optional[str]) -> None:
        """`old` None: activar. "" : cambiar sin tenerla en el llavero (se pregunta). Otro: cambiar."""
        enabling = old is None
        if enabling:
            heading = "Activar la copia cifrada"
            body = ("Así la copia guarda también las contraseñas, los datos de Salud y los de WhatsApp.\n\n"
                    "Te recomendamos usar la misma contraseña con la que entras en el ordenador: así solo "
                    "tienes que recordar una. Se guardará en el llavero del ordenador.\n\n"
                    "La próxima copia será completa y puede tardar varias horas. La copia actual se conserva "
                    "hasta que la nueva termine.")
        else:
            heading = "Cambiar la contraseña de las copias"
            body = ("Si has cambiado la contraseña con la que entras en el ordenador, escribe aquí la nueva "
                    "para que sigan siendo la misma.")
        dialog = Adw.AlertDialog(heading=heading, body=body)
        group = Adw.PreferencesGroup()
        old_row = Adw.PasswordEntryRow(title="Contraseña actual de las copias")
        if old == "":
            group.add(old_row)
        new_row = Adw.PasswordEntryRow(title="Contraseña nueva")
        repeat_row = Adw.PasswordEntryRow(title="Repítela")
        group.add(new_row)
        group.add(repeat_row)
        problem_label = Gtk.Label(css_classes=["error"], wrap=True, margin_top=6, visible=False)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(group)
        content.append(problem_label)
        dialog.set_extra_child(content)

        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("ok", "Activar" if enabling else "Cambiar")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("ok", False)
        dialog.set_close_response("cancel")

        def validate(*_args) -> None:
            new, repeated = new_row.get_text(), repeat_row.get_text()
            problem = encryption.check_new_password(new, repeated)
            if old == "" and not old_row.get_text():
                problem = problem or "Falta la contraseña actual."
            # No regañar mientras se está escribiendo la segunda
            problem_label.set_visible(bool(problem and repeated))
            problem_label.set_label(problem or "")
            dialog.set_response_enabled("ok", problem is None)

        for row in (old_row, new_row, repeat_row):
            row.connect("changed", validate)

        def on_response(_dialog, response: str) -> None:
            if response == "ok":
                self._set_password(info, new_row.get_text(), old if old else old_row.get_text())

        dialog.connect("response", on_response)
        dialog.present(self)

    def _set_password(self, info: device.DeviceInfo, new: str, old: str) -> None:
        self.busy = True
        self.working_page.set_title("Cambiando la contraseña…" if old else "Activando el cifrado…")
        self.stack.set_visible_child_name("working")

        async def change() -> None:
            await encryption.set_password(info.udid, self.settings.root, new, old=old)
            await asyncio.to_thread(encryption.store_password, info.udid, info.name, new)

        def done(_result, exc: Optional[BaseException]) -> None:
            self.busy = False
            if exc is not None:
                error = friendly(exc)
                self._tell(error.title, error.hint)
            elif old:
                self._tell("Contraseña cambiada", "Se ha guardado en el llavero del ordenador.")
            else:
                self._tell("Copia cifrada activada",
                           "La contraseña se ha guardado en el llavero del ordenador.\n\n"
                           "Ahora pulsa «Hacer copia ahora». Será una copia completa y puede tardar varias horas: "
                           "deja el portátil enchufado.")
            if self.status:
                self._apply_status(self.status)
            self._poll()

        self._submit(change(), done)

    # --- Copias guardadas ---

    def _refresh_backups(self) -> None:
        root = self.settings.root
        self.folder_row.set_subtitle(f"{root}\n{fmt.size(storage.free_space(root))} libres")
        self._submit(asyncio.to_thread(storage.list_backups, root), self._on_backups)

    def _on_backups(self, entries: Optional[list], _exc: Optional[BaseException]) -> None:
        self.backups = entries or []
        for row in self.backup_rows:
            self.backups_group.remove(row)
        self.backup_rows = []
        if not self.backups:
            row = Adw.ActionRow(title="Todavía no hay ninguna copia")
            self.backups_group.add(row)
            self.backup_rows.append(row)
        for entry in self.backups:
            row = self._backup_row(entry)
            self.backups_group.add(row)
            self.backup_rows.append(row)
        self._update_last_backup()

    def _backup_row(self, entry: storage.BackupEntry) -> Adw.ActionRow:
        state = "cifrada" if entry.encrypted else "sin cifrar"
        base = f"{fmt.ago(entry.date)} · iOS {entry.ios_version} · {state}"
        if not entry.complete:
            base = f"No terminó · {base}"
        title = f"{entry.device_name} (copia anterior)" if entry.set_aside else entry.device_name
        row = Adw.ActionRow(title=title, subtitle=base)
        row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic" if entry.complete else "dialog-warning-symbolic"))
        open_button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                                 tooltip_text="Abrir carpeta", css_classes=["flat"])
        open_button.connect("clicked", lambda _b: Gtk.FileLauncher(file=Gio.File.new_for_path(str(entry.path))).launch(self, None, None))
        row.add_suffix(open_button)
        if entry.set_aside and not self.job:
            delete_button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                       tooltip_text="Borrar esta copia", css_classes=["flat"])
            delete_button.connect("clicked", lambda _b: self._confirm_delete(entry.path))
            row.add_suffix(delete_button)
        # Medir cientos de miles de ficheros tarda: se rellena cuando acaba
        self._submit(asyncio.to_thread(storage.directory_size, entry.path),
                     lambda total, _e: row.set_subtitle(f"{base} · {fmt.size(total)}") if total else None)
        return row

    def _update_last_backup(self) -> None:
        info = self.status.info if self.status else None
        if info is None:
            return
        entry = next((e for e in self.backups if e.udid == info.udid and e.complete), None)
        entry = entry or next((e for e in self.backups if e.udid == info.udid), None)
        if entry is None:
            self.last_backup_label.set_label("Todavía no hay ninguna copia de este iPhone")
        elif not entry.complete:
            self.last_backup_label.set_label("La última copia no terminó")
        else:
            self.last_backup_label.set_label(f"Última copia: {fmt.ago(entry.date)}")
        root = self.settings.root
        if encryption_changed(root, info.udid, info.encrypted):
            self.backup_kind_label.set_label(
                "Como ha cambiado el cifrado, la próxima copia será completa y puede tardar varias horas. "
                "La copia actual se conserva hasta que la nueva termine."
            )
        elif has_previous_backup(root, info.udid):
            self.backup_kind_label.set_label("Solo se copiará lo que ha cambiado desde la última vez.")
        else:
            self.backup_kind_label.set_label("La primera copia puede tardar varias horas.")

    def _confirm_delete(self, path: Optional[Path]) -> None:
        if path is None:
            return

        def delete() -> None:
            self._submit(asyncio.to_thread(shutil.rmtree, path), deleted)

        def deleted(_result, exc: Optional[BaseException]) -> None:
            if exc is not None:
                self._tell("No se ha podido borrar", f"{exc}")
            if path == self.result_aside:
                self.result_aside = None
                self.delete_aside_button.set_visible(False)
            self._refresh_backups()

        self._ask("¿Borrar la copia anterior?",
                  f"Se borrará {path.name} para siempre. La copia nueva no se toca.",
                  "Borrar", delete, destructive=True)

    def _on_change_folder(self, _button) -> None:
        dialog = Gtk.FileDialog(title="Elige dónde guardar las copias",
                                initial_folder=Gio.File.new_for_path(str(self.settings.root)))

        def chosen(dialog: Gtk.FileDialog, result) -> None:
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return  # cancelado
            self.settings.backup_root = folder.get_path()
            settings.save(self.settings)
            self._refresh_backups()

        dialog.select_folder(self, None, chosen)

    # --- Copia ---

    def _on_backup_clicked(self, _button) -> None:
        info = self.status.info if self.status else None
        if info is None:
            return
        root = self.settings.root
        needed = storage.space_needed(info.used, next_backup_is_incremental(root, info.udid, info.encrypted))
        free = storage.free_space(root)
        if free < needed:
            self._tell("No hay espacio suficiente",
                       f"Hacen falta unos {fmt.size(needed)} libres y solo hay {fmt.size(free)}.\n"
                       "Libera espacio o elige otra carpeta, por ejemplo en un disco externo.")
            return
        if power.on_battery():
            self._ask("El portátil va con batería",
                      "La copia puede tardar mucho. Si se queda sin batería a mitad, habrá que repetirla.",
                      "Empezar igualmente", lambda: self._start_backup(info), cancel_label="Esperar")
            return
        self._start_backup(info)

    def _start_backup(self, info: device.DeviceInfo) -> None:
        self.job = BackupJob(self.settings.root, lambda snap: on_main(self._render_progress, snap),
                             udid=info.udid)
        # Si el portátil se duerme a mitad, la copia se cuelga sin avisar
        self.inhibit_cookie = self.get_application().inhibit(
            self, Gtk.ApplicationInhibitFlags.SUSPEND | Gtk.ApplicationInhibitFlags.IDLE, "Copia del iPhone en curso"
        )
        self.cancel_button.set_sensitive(True)
        self.cancel_button.set_label("Cancelar")
        self._render_progress(self.job.snapshot)
        self.stack.set_visible_child_name("progress")
        self._submit(self.job.run(), self._on_backup_finished)

    def _render_progress(self, snap: Snapshot) -> None:
        if self.job is None or snap.phase.finished:
            return
        icon, title, description = PROGRESS_TEXT[snap.phase]
        self.progress_page.set_icon_name(icon)
        self.progress_page.set_title(title)
        if snap.phase == Phase.COPYING and not snap.received:
            # Antes de mandar nada el iPhone puede pasarse un par de minutos preparando los datos
            self._start_pulse()
            description = "El iPhone está preparando los datos. Puede tardar un par de minutos."
            self.progress_details.set_label("")
        elif snap.phase == Phase.COPYING:
            self._stop_pulse()
            if snap.percent is not None:
                self.progress_bar.set_fraction(snap.percent / 100)
                description = f"{snap.percent:.0f} %"
                self.set_title(f"{snap.percent:.0f} % · {APP_NAME}")
            else:
                self.progress_bar.pulse()
            # Al empezar una incremental casi no llegan datos (el iPhone lee el índice antiguo): «333 B · 18 B/s» confunde
            if snap.received < 10**6:
                details = "Comparando con la copia anterior…"
            else:
                details = f"{fmt.size(snap.received)} copiados · {fmt.size(snap.speed)}/s"
            if snap.incremental:
                details += "\nSolo se copia lo que ha cambiado."
            else:
                details += f" · quedan {fmt.duration(snap.eta)}"
            self.progress_details.set_label(details)
        else:
            self._start_pulse()
            self.progress_details.set_label("")
        self.progress_page.set_description(description)
        self.stalled_label.set_visible(snap.stalled)

    def _start_pulse(self) -> None:
        if not self.pulse_source:
            self.pulse_source = GLib.timeout_add(150, lambda: self.progress_bar.pulse() or GLib.SOURCE_CONTINUE)

    def _stop_pulse(self) -> None:
        if self.pulse_source:
            GLib.source_remove(self.pulse_source)
            self.pulse_source = 0

    def _confirm_cancel(self, then: Optional[Callable[[], None]] = None) -> None:
        def cancel() -> None:
            if self.job:
                self.job.cancel()
                self.cancel_button.set_sensitive(False)
                self.cancel_button.set_label("Cancelando…")
            if then:
                then()

        self._ask("¿Cancelar la copia?", "La copia no quedará completa y habrá que hacerla otra vez.",
                  "Cancelar copia", cancel, cancel_label="Seguir copiando", destructive=True)

    def _on_backup_finished(self, snap: Optional[Snapshot], exc: Optional[BaseException]) -> None:
        self._stop_pulse()
        if self.inhibit_cookie:
            self.get_application().uninhibit(self.inhibit_cookie)
            self.inhibit_cookie = 0
        self.job = None
        self.set_title(APP_NAME)
        if exc is not None or snap is None:
            snap = Snapshot(Phase.FAILED, error=friendly(exc or RuntimeError("sin resultado")))

        if snap.phase == Phase.DONE:
            icon, title = "emblem-ok-symbolic", "¡Copia terminada!"
            body = f"Guardada en {self.settings.root}.\nYa puedes desconectar el iPhone."
            if snap.set_aside:
                body += "\n\nLa copia anterior sin cifrar sigue guardada. Ya no hace falta: puedes borrarla."
        elif snap.phase == Phase.CANCELLED:
            icon, title = "process-stop-symbolic", "Copia cancelada"
            body = snap.hint or "La copia no se ha completado."
        else:
            icon, title = "dialog-error-symbolic", snap.error.title
            body = snap.error.hint
        self.result_page.set_icon_name(icon)
        self.result_page.set_title(title)
        self.result_page.set_description(body)
        self.result_aside = snap.set_aside if snap.phase == Phase.DONE else None
        self.delete_aside_button.set_visible(self.result_aside is not None)
        self.showing_result = True
        self.stack.set_visible_child_name("result")
        self._refresh_backups()

        if snap.phase != Phase.CANCELLED and not self.is_active():
            notification = Gio.Notification.new(title)
            notification.set_body(body)
            self.get_application().send_notification("backup-finished", notification)
        if self.close_after_backup:
            self.close()

    def _on_result_back(self, _button) -> None:
        self.showing_result = False
        if self.status:
            self._apply_status(self.status)
        self._poll()

    def _on_close_request(self, _window) -> bool:
        if self.job is None:
            return False
        self.close_after_backup = False

        def close_when_cancelled() -> None:
            self.close_after_backup = True

        self._confirm_cancel(then=close_when_cancelled)
        return True  # no cerrar todavía
