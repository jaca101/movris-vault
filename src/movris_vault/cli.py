"""Línea de órdenes: para probar por SSH y para quien prefiera la terminal."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import signal
import sys
from pathlib import Path

from . import fmt
from .core import device, encryption, power, settings, storage
from .core.backup import BackupJob, Phase, Snapshot, encryption_changed, next_backup_is_incremental
from .core.errors import FriendlyError, friendly
from .i18n import _, set_language


def phase_text(phase: Phase) -> str:
    return {
        Phase.CONNECTING: _("Connecting to the iPhone…"),
        Phase.WAITING_PASSCODE: _("Enter your passcode on the iPhone so the backup can start."),
        Phase.COPYING: _("Copying…"),
        Phase.DONE: "✅ " + _("Backup finished."),
        Phase.CANCELLED: _("Backup cancelled."),
    }.get(phase, "")


def print_status(status: device.DeviceStatus) -> None:
    if status.info is None:
        print({
            device.DeviceState.ABSENT: _("No iPhone connected."),
            device.DeviceState.RECONNECT: _("The iPhone is plugged in but not responding: unplug the cable and plug it back in."),
            device.DeviceState.UNTRUSTED: _("The iPhone doesn't trust this computer yet (use `pair`)."),
        }[status.state])
        return
    info = status.info
    print(f"{info.name} ({info.product_type}, iOS {info.ios_version})")
    print(_("Used: {used} of {capacity}").format(used=fmt.size(info.used), capacity=fmt.size(info.capacity)))
    print(_("Encrypted backup: yes") if info.encrypted else _("Encrypted backup: no"))


async def cmd_info(_args) -> int:
    status = await device.probe()
    print_status(status)
    return 0 if status.state == device.DeviceState.READY else 1


async def cmd_pair(_args) -> int:
    print(_("Look at the iPhone: tap “Trust” and enter your passcode."))
    lockdown = await device.connect(pair=True, pair_timeout=120)
    await lockdown.close()
    print(_("Paired."))
    return 0


async def cmd_encrypt(args) -> int:
    lockdown = await device.connect()
    try:
        info = await device.read_info(lockdown)
    finally:
        await lockdown.close()
    old = ""
    if info.encrypted:
        old = encryption.lookup_password(info.udid) or getpass.getpass(_("Current backup password: "))
    print(_("Tip: use the same password you use to log in to this computer, so there's only one to remember."))
    new = getpass.getpass(_("New password: "))
    problem = encryption.check_new_password(new, getpass.getpass(_("Repeat it: ")))
    if problem:
        print(problem)
        return 1
    print(_("If the iPhone asks for your passcode, enter it."))
    await encryption.set_password(info.udid, args.dest, new, old=old)
    encryption.store_password(info.udid, info.name, new)
    print(_("Password changed.") if old else _("Encrypted backup turned on and password saved in the keyring."))
    return 0


def cmd_list(args) -> int:
    entries = storage.list_backups(args.dest)
    if not entries:
        print(_("No backups in {folder}").format(folder=args.dest))
    for e in entries:
        state = _("complete") if e.complete else _("INCOMPLETE")
        encrypted = _("encrypted") if e.encrypted else _("not encrypted")
        print(f"{e.device_name} · iOS {e.ios_version} · {fmt.ago(e.date)} · {state} · {encrypted}")
        print(f"   {e.path}")
    return 0


async def cmd_backup(args) -> int:
    status = await device.probe()
    if status.state != device.DeviceState.READY:
        print_status(status)
        return 1
    info = status.info
    previous = next_backup_is_incremental(args.dest, info.udid, info.encrypted) and not args.full
    needed = storage.space_needed(info.used, previous)
    free = storage.free_space(args.dest)
    if free < needed:
        print(_("Not enough space: about {needed} must be free and there is {free}.").format(
            needed=fmt.size(needed), free=fmt.size(free)))
        return 1
    if power.on_battery():
        print("⚠️  " + _("The laptop is on battery: plug it in so it doesn't run out halfway."))
    kind = _("Incremental backup of {device} in {folder}") if previous else _("Full backup of {device} in {folder}")
    print(kind.format(device=info.name, folder=args.dest))
    if encryption_changed(args.dest, info.udid, info.encrypted):
        print(_("Encryption has changed: the previous backup is set aside and put back if this one doesn't finish."))

    last_line = [""]

    def on_update(snap: Snapshot) -> None:
        if snap.phase == Phase.COPYING and not snap.received:
            line = _("The iPhone is preparing the data (this can take a couple of minutes)…")
        elif snap.phase == Phase.COPYING:
            percent = "—" if snap.percent is None else fmt.percent(snap.percent, 1)
            line = _("{percent} · {received} · {speed}/s · {eta} left").format(
                percent=percent, received=fmt.size(snap.received), speed=fmt.size(snap.speed),
                eta=fmt.duration(snap.eta))
        else:
            line = phase_text(snap.phase)
        if snap.stalled:
            line += "  " + _("(seems stuck: if it doesn't move, unplug the cable and plug it back in)")
        if line and line != last_line[0]:
            last_line[0] = line
            print(line, flush=True)

    job = BackupJob(args.dest, on_update, udid=info.udid, full=args.full)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, job.cancel)
    with power.session_inhibit(_("iPhone backup in progress")):
        snap = await job.run()
    if snap.error:
        print(f"❌ {snap.error.title}\n   {snap.error.hint}")
    if snap.hint:
        print(snap.hint)
    if snap.phase == Phase.DONE and snap.set_aside:
        print(_("The previous backup is still in {path}; delete it whenever you like.").format(path=snap.set_aside))
    return 0 if snap.phase == Phase.DONE else 1


def main(argv: list[str] | None = None) -> int:
    set_language(settings.load().language)
    parser = argparse.ArgumentParser(prog="movris-vault", description=_("Encrypted iPhone backups for Linux."))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("info", help=_("details of the connected iPhone"))
    sub.add_parser("pair", help=_("pair (tap “Trust” on the iPhone)"))
    for name, help_text in (("list", _("saved backups")), ("backup", _("make a backup")),
                            ("encrypt", _("turn on encrypted backups or change their password"))):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dest", type=Path, default=settings.load().root, help=_("backup folder"))
        if name == "backup":
            p.add_argument("--full", action="store_true", help=_("full backup even if one already exists"))
    args = parser.parse_args(argv)
    # pymobiledevice3 avisa en inglés de cosas que ya contamos nosotros
    logging.getLogger("pymobiledevice3").setLevel(logging.ERROR)

    try:
        if args.command == "info":
            return asyncio.run(cmd_info(args))
        if args.command == "pair":
            return asyncio.run(cmd_pair(args))
        if args.command == "list":
            return cmd_list(args)
        if args.command == "backup":
            return asyncio.run(cmd_backup(args))
        if args.command == "encrypt":
            return asyncio.run(cmd_encrypt(args))
    except Exception as exc:
        error: FriendlyError = friendly(exc)
        print(f"❌ {error.title}\n   {error.hint}", file=sys.stderr)
        return 1

    from .ui.app import run  # la ventana solo se carga si hace falta (necesita GTK 4)

    return run()
