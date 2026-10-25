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
from .core import device, encryption, power, storage
from .core.backup import BackupJob, Phase, Snapshot, encryption_changed, next_backup_is_incremental
from .core.errors import FriendlyError, friendly

PHASE_TEXT = {
    Phase.CONNECTING: "Conectando con el iPhone…",
    Phase.WAITING_PASSCODE: "Escribe tu código en el iPhone para que empiece la copia.",
    Phase.COPYING: "Copiando…",
    Phase.DONE: "✅ Copia terminada.",
    Phase.CANCELLED: "Copia cancelada.",
}


def print_status(status: device.DeviceStatus) -> None:
    messages = {
        device.DeviceState.ABSENT: "No hay ningún iPhone conectado.",
        device.DeviceState.RECONNECT: "El iPhone está enchufado pero no responde: desconecta y vuelve a conectar el cable.",
        device.DeviceState.UNTRUSTED: "El iPhone no confía todavía en este ordenador (usa `pair`).",
    }
    if status.info is None:
        print(messages[status.state])
        return
    info = status.info
    print(f"{info.name} ({info.product_type}, iOS {info.ios_version})")
    print(f"Usado: {fmt.size(info.used)} de {fmt.size(info.capacity)}")
    print(f"Copia cifrada: {'sí' if info.encrypted else 'no'}")


async def cmd_info(_args) -> int:
    status = await device.probe()
    print_status(status)
    return 0 if status.state == device.DeviceState.READY else 1


async def cmd_pair(_args) -> int:
    print("Mira el iPhone: pulsa «Confiar» y escribe tu código.")
    lockdown = await device.connect(pair=True, pair_timeout=120)
    await lockdown.close()
    print("Emparejado.")
    return 0


async def cmd_encrypt(args) -> int:
    lockdown = await device.connect()
    try:
        info = await device.read_info(lockdown)
    finally:
        await lockdown.close()
    old = ""
    if info.encrypted:
        old = encryption.lookup_password(info.udid) or getpass.getpass("Contraseña actual de las copias: ")
    print("Consejo: usa la misma contraseña con la que entras en el ordenador; así solo tienes que recordar una.")
    new = getpass.getpass("Contraseña nueva: ")
    problem = encryption.check_new_password(new, getpass.getpass("Repítela: "))
    if problem:
        print(problem)
        return 1
    print("Si el iPhone te pide el código, escríbelo.")
    await encryption.set_password(info.udid, args.dest, new, old=old)
    encryption.store_password(info.udid, info.name, new)
    print("Copia cifrada activada y contraseña guardada en el llavero." if not old else "Contraseña cambiada.")
    return 0


def cmd_list(args) -> int:
    entries = storage.list_backups(args.dest)
    if not entries:
        print(f"No hay copias en {args.dest}")
    for e in entries:
        state = "completa" if e.complete else "INCOMPLETA"
        print(f"{e.device_name} · iOS {e.ios_version} · {fmt.ago(e.date)} · {state} · {'cifrada' if e.encrypted else 'sin cifrar'}")
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
        print(f"Falta espacio: hacen falta unos {fmt.size(needed)} libres y hay {fmt.size(free)}.")
        return 1
    if power.on_battery():
        print("⚠️  El portátil va con batería: enchúfalo para que no se apague a mitad.")
    print(f"Copia {'incremental' if previous else 'completa'} de {info.name} en {args.dest}")
    if encryption_changed(args.dest, info.udid, info.encrypted):
        print("El cifrado ha cambiado: la copia anterior se aparta y vuelve a su sitio si esta no termina.")

    last_line = [""]

    def on_update(snap: Snapshot) -> None:
        if snap.phase == Phase.COPYING and not snap.received:
            line = "El iPhone está preparando los datos (puede tardar un par de minutos)…"
        elif snap.phase == Phase.COPYING:
            percent = "—" if snap.percent is None else f"{snap.percent:.1f} %".replace(".", ",")
            line = f"{percent} · {fmt.size(snap.received)} · {fmt.size(snap.speed)}/s · quedan {fmt.duration(snap.eta)}"
        else:
            line = PHASE_TEXT.get(snap.phase, "")
        if snap.stalled:
            line += "  (parece atascado: si no avanza, desconecta y vuelve a conectar el cable)"
        if line and line != last_line[0]:
            last_line[0] = line
            print(line, flush=True)

    job = BackupJob(args.dest, on_update, udid=info.udid, full=args.full)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, job.cancel)
    with power.session_inhibit("Copia del iPhone en curso"):
        snap = await job.run()
    if snap.error:
        print(f"❌ {snap.error.title}\n   {snap.error.hint}")
    if snap.hint:
        print(snap.hint)
    if snap.phase == Phase.DONE and snap.set_aside:
        print(f"La copia anterior sigue en {snap.set_aside}; bórrala cuando quieras.")
    return 0 if snap.phase == Phase.DONE else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="movris-vault", description="Copias de seguridad del iPhone.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("info", help="datos del iPhone conectado")
    sub.add_parser("pair", help="emparejar (pulsar «Confiar» en el iPhone)")
    for name, help_text in (("list", "copias guardadas"), ("backup", "hacer una copia"),
                            ("encrypt", "activar la copia cifrada o cambiar su contraseña")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dest", type=Path, default=storage.DEFAULT_ROOT, help="carpeta de copias")
        if name == "backup":
            p.add_argument("--full", action="store_true", help="copia completa aunque ya exista una")
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
