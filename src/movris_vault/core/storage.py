"""Las copias guardadas en disco: listarlas, medirlas y comprobar el espacio libre."""

from __future__ import annotations

import os
import plistlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_ROOT = Path.home() / "iPhoneBackup"

# Margen para Manifest.db, ficheros que crecen durante la copia, etc.
SPACE_MARGIN = 5 * 10**9


@dataclass(frozen=True)
class BackupEntry:
    path: Path
    udid: str
    device_name: str
    ios_version: str
    date: Optional[datetime]   # en UTC
    complete: bool
    encrypted: bool

    @property
    def set_aside(self) -> bool:
        """Una copia anterior apartada (ver `set_aside`), no la que se va actualizando."""
        return self.path.name != self.udid


def _load_plist(path: Path) -> dict:
    try:
        with open(path, "rb") as fd:
            data = plistlib.load(fd)
        return data if isinstance(data, dict) else {}
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}


def read_backup(path: Path) -> Optional[BackupEntry]:
    info = _load_plist(path / "Info.plist")
    if not info:
        return None
    status = _load_plist(path / "Status.plist")
    manifest = _load_plist(path / "Manifest.plist")
    date = status.get("Date") or info.get("Last Backup Date")
    if isinstance(date, datetime) and date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return BackupEntry(
        path=path,
        udid=str(info.get("Unique Identifier") or info.get("Target Identifier") or path.name),
        device_name=str(info.get("Device Name") or info.get("Display Name") or path.name),
        ios_version=str(info.get("Product Version") or ""),
        date=date if isinstance(date, datetime) else None,
        # Manifest.db solo existe cuando la copia llegó al final al menos una vez
        complete=status.get("SnapshotState") == "finished" and (path / "Manifest.db").is_file(),
        encrypted=bool(manifest.get("IsEncrypted", False)),
    )


def list_backups(root: Path = DEFAULT_ROOT) -> list[BackupEntry]:
    if not root.is_dir():
        return []
    entries = [e for p in root.iterdir() if p.is_dir() and (e := read_backup(p))]
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(entries, key=lambda e: e.date or oldest, reverse=True)


def find_backup(root: Path, udid: str) -> Optional[BackupEntry]:
    path = root / udid
    return read_backup(path) if path.is_dir() else None


def set_aside(path: Path, label: str) -> Path:
    """Aparta una copia (renombrándola, al instante) para que una copia completa nueva no la pise."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = path.with_name(f"{path.name}.{label}-{stamp}")
    path.rename(target)
    return target


def put_back(aside: Path, original: Path) -> None:
    """Deshace `set_aside`: quita la copia a medias y devuelve la anterior a su sitio."""
    if original.exists():
        shutil.rmtree(original)
    aside.rename(original)


def directory_size(path: Path) -> int:
    """Lento con cientos de miles de ficheros: llamarlo fuera del hilo de la interfaz."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def free_space(root: Path) -> int:
    # La carpeta puede no existir todavía: medimos en la primera que sí exista
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def space_needed(device_used: int, has_previous: bool) -> int:
    """Lo que hace falta libre antes de empezar.

    Una copia incremental solo trae lo que ha cambiado, pero no sabemos cuánto es hasta empezar;
    pedimos una décima parte de lo usado en el iPhone, que cubre de sobra el uso normal.
    """
    expected = device_used // 10 if has_previous else device_used
    return expected + SPACE_MARGIN
