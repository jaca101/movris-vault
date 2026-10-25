"""Traduce los fallos técnicos a mensajes que entienda cualquiera."""

from __future__ import annotations

import errno

from pymobiledevice3.exceptions import (
    ConnectionFailedToUsbmuxdError,
    ConnectionTerminatedError,
    NoDeviceConnectedError,
    NotEnoughDiskSpaceError,
    NotPairedError,
    PairingDialogResponsePendingError,
    PasscodeRequiredError,
    PasswordRequiredError,
    UserDeniedPairingError,
)


class FriendlyError(Exception):
    """Error con un título corto y una pista de qué hacer."""

    def __init__(self, title: str, hint: str = "") -> None:
        super().__init__(title)
        self.title = title
        self.hint = hint


DISCONNECTED = FriendlyError(
    "Se ha perdido la conexión con el iPhone",
    "Comprueba que el cable sigue bien conectado y vuelve a intentarlo.",
)
CANCELLED_ON_DEVICE = FriendlyError(
    "La copia se ha cancelado desde el iPhone",
    "Si no querías cancelarla, vuelve a empezar y no toques el iPhone hasta que termine.",
)


def friendly(exc: BaseException) -> FriendlyError:
    if isinstance(exc, FriendlyError):
        return exc
    if isinstance(exc, (NoDeviceConnectedError, ConnectionFailedToUsbmuxdError)):
        return FriendlyError("No encuentro ningún iPhone", "Conéctalo con el cable USB y desbloquéalo.")
    if isinstance(exc, ConnectionTerminatedError):
        return DISCONNECTED
    if isinstance(exc, (PasswordRequiredError, PasscodeRequiredError)):
        return FriendlyError("El iPhone está bloqueado", "Desbloquéalo con tu código y vuelve a intentarlo.")
    if isinstance(exc, UserDeniedPairingError):
        return FriendlyError(
            "En el iPhone se pulsó «No confiar»",
            "Desconecta y vuelve a conectar el cable, y esta vez pulsa «Confiar».",
        )
    if isinstance(exc, (NotPairedError, PairingDialogResponsePendingError)):
        return FriendlyError("El iPhone todavía no confía en este ordenador", "Pulsa «Confiar» en el iPhone.")
    if isinstance(exc, NotEnoughDiskSpaceError) or (isinstance(exc, OSError) and exc.errno == errno.ENOSPC):
        return FriendlyError(
            "No queda espacio en el disco",
            "Libera espacio o elige otra carpeta, por ejemplo en un disco externo.",
        )
    if isinstance(exc, PermissionError):
        return FriendlyError("No puedo escribir en la carpeta de copias", f"Revisa los permisos de {exc.filename}.")
    return FriendlyError("La copia no se ha podido completar", f"Detalle técnico: {type(exc).__name__}: {exc}")
