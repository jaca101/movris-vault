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

from ..i18n import _


class FriendlyError(Exception):
    """Error con un título corto y una pista de qué hacer (ya traducidos)."""

    def __init__(self, title: str, hint: str = "") -> None:
        super().__init__(title)
        self.title = title
        self.hint = hint


# Funciones y no constantes: el texto tiene que salir en el idioma elegido en ese momento

def disconnected() -> FriendlyError:
    return FriendlyError(
        _("Lost the connection to the iPhone"),
        _("Check that the cable is still plugged in and try again."),
    )


def cancelled_on_device() -> FriendlyError:
    return FriendlyError(
        _("The backup was cancelled on the iPhone"),
        _("If you didn't mean to, start again and don't touch the iPhone until it finishes."),
    )


def friendly(exc: BaseException) -> FriendlyError:
    if isinstance(exc, FriendlyError):
        return exc
    if isinstance(exc, (NoDeviceConnectedError, ConnectionFailedToUsbmuxdError)):
        return FriendlyError(_("No iPhone found"), _("Plug it in with the USB cable and unlock it."))
    if isinstance(exc, ConnectionTerminatedError):
        return disconnected()
    if isinstance(exc, (PasswordRequiredError, PasscodeRequiredError)):
        return FriendlyError(_("The iPhone is locked"), _("Unlock it with your passcode and try again."))
    if isinstance(exc, UserDeniedPairingError):
        return FriendlyError(
            _("“Don't Trust” was tapped on the iPhone"),
            _("Unplug the cable, plug it back in and tap “Trust” this time."),
        )
    if isinstance(exc, (NotPairedError, PairingDialogResponsePendingError)):
        return FriendlyError(_("The iPhone doesn't trust this computer yet"), _("Tap “Trust” on the iPhone."))
    if isinstance(exc, NotEnoughDiskSpaceError) or (isinstance(exc, OSError) and exc.errno == errno.ENOSPC):
        return FriendlyError(
            _("The disk is full"),
            _("Free up some space or choose another folder, for example on an external disk."),
        )
    if isinstance(exc, PermissionError):
        return FriendlyError(
            _("Can't write to the backup folder"),
            _("Check the permissions of {path}.").format(path=exc.filename),
        )
    return FriendlyError(
        _("The backup couldn't be completed"),
        _("Technical detail: {detail}").format(detail=f"{type(exc).__name__}: {exc}"),
    )
