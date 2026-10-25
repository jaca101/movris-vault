"""Copia cifrada: activar o cambiar la contraseña en el iPhone y guardarla en el llavero.

Para hacer la copia no hace falta la contraseña (el iPhone cifra por su cuenta). Hace falta para
restaurarla o para cambiarla, y por eso se guarda en el llavero de la sesión, que se abre con la
contraseña de Linux.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pymobiledevice3.exceptions import PyMobileDevice3Exception

from . import device
from .backup import _ObservedBackupService
from ..i18n import _
from .errors import FriendlyError

SCHEMA_NAME = "io.github.jaca101.MovrisVault.BackupPassword"
# Nombre de antes de llamarse Movris Vault: las contraseñas guardadas entonces se pasan al nuevo al leerlas
LEGACY_SCHEMA_NAMES = ("io.github.jaca101.CopiaIphone.BackupPassword",)
MIN_LENGTH = 4


def check_new_password(password: str, repeated: str) -> Optional[str]:
    """Qué le pasa a la contraseña nueva, o None si vale."""
    if len(password) < MIN_LENGTH:
        return _("It must have at least {n} characters.").format(n=MIN_LENGTH)
    if password != repeated:
        return _("The two passwords don't match.")
    return None


# --- Llavero (libsecret). Las llamadas pueden abrir la ventana de desbloqueo: fuera del hilo de la interfaz ---

def _secret(name: str = SCHEMA_NAME):
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    schema = Secret.Schema.new(name, Secret.SchemaFlags.NONE, {"udid": Secret.SchemaAttributeType.STRING})
    return Secret, schema


def store_password(udid: str, device_name: str, password: str) -> None:
    Secret, schema = _secret()
    Secret.password_store_sync(
        schema, {"udid": udid}, Secret.COLLECTION_DEFAULT,
        _("Backup password for {device} (Movris Vault)").format(device=device_name), password, None,
    )


def lookup_password(udid: str) -> Optional[str]:
    Secret, schema = _secret()
    password = Secret.password_lookup_sync(schema, {"udid": udid}, None)
    if password is not None:
        return password
    for legacy in LEGACY_SCHEMA_NAMES:
        Secret, old_schema = _secret(legacy)
        password = Secret.password_lookup_sync(old_schema, {"udid": udid}, None)
        if password is not None:
            store_password(udid, "iPhone", password)
            Secret.password_clear_sync(old_schema, {"udid": udid}, None)
            return password
    return None


# --- iPhone ---

async def set_password(udid: str, backup_root: Path, new: str, old: str = "") -> None:
    """Activa el cifrado (sin `old`) o cambia la contraseña. El iPhone puede pedir su código."""
    lockdown = await device.connect(udid)
    try:
        async with _ObservedBackupService(lockdown, job=None) as service:
            await service.change_password(backup_root, old=old, new=new)
    except PyMobileDevice3Exception as exc:
        if "device reported an error" not in str(exc):
            raise
        if old:
            raise FriendlyError(
                _("The iPhone didn't accept the change"),
                _("The current password may not be the right one."),
            ) from exc
        raise FriendlyError(
            _("The iPhone didn't turn on encryption"),
            _("Unlock it, enter your passcode if it asks for it, and try again."),
        ) from exc
    finally:
        await lockdown.close()
