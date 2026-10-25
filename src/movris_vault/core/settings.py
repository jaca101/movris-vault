"""Ajustes del usuario en ~/.config/movris-vault/settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import storage

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "movris-vault"


@dataclass
class Settings:
    backup_root: str = str(storage.DEFAULT_ROOT)

    @property
    def root(self) -> Path:
        return Path(self.backup_root)


def load(config_dir: Path = CONFIG_DIR) -> Settings:
    try:
        data = json.loads((config_dir / "settings.json").read_text())
    except (OSError, ValueError):
        return Settings()
    known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
    return Settings(**known)


def save(settings: Settings, config_dir: Path = CONFIG_DIR) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    tmp = config_dir / "settings.json.tmp"
    tmp.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False))
    tmp.replace(config_dir / "settings.json")
