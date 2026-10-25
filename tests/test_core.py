import plistlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymobiledevice3.exceptions import ConnectionTerminatedError, NotEnoughDiskSpaceError

from movris_vault import fmt
from movris_vault.core import device, power, storage
from movris_vault.core.backup import ProgressEstimator, has_previous_backup
from movris_vault.core.errors import DISCONNECTED, friendly

UDID = "00008000-000A1B2C3D4E5F60"


def make_backup(root: Path, udid: str = UDID, finished: bool = True, encrypted: bool = False) -> Path:
    path = root / udid
    path.mkdir(parents=True)
    with open(path / "Info.plist", "wb") as fd:
        plistlib.dump({"Device Name": "iPhone de Ana", "Product Version": "27.0.1", "Unique Identifier": udid}, fd)
    with open(path / "Status.plist", "wb") as fd:
        state = "finished" if finished else "new"
        plistlib.dump({"SnapshotState": state, "Date": datetime(2026, 10, 25, 14, 47)}, fd, fmt=plistlib.FMT_BINARY)
    with open(path / "Manifest.plist", "wb") as fd:
        plistlib.dump({"IsEncrypted": encrypted}, fd)
    if finished:
        (path / "Manifest.db").write_bytes(b"x" * 10)
    return path


def write_sysfs(root: Path, name: str, **files: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    for key, value in files.items():
        (d / key).write_text(value + "\n")


# --- storage ---

def test_list_backups_reads_metadata(tmp_path):
    make_backup(tmp_path)
    (tmp_path / "ultima_copia.log").write_text("log")
    [entry] = storage.list_backups(tmp_path)
    assert entry.device_name == "iPhone de Ana"
    assert entry.ios_version == "27.0.1"
    assert entry.complete and not entry.encrypted
    assert entry.date == datetime(2026, 10, 25, 14, 47, tzinfo=timezone.utc)


def test_unfinished_backup_is_incomplete(tmp_path):
    make_backup(tmp_path, finished=False)
    [entry] = storage.list_backups(tmp_path)
    assert not entry.complete


def test_list_backups_missing_root(tmp_path):
    assert storage.list_backups(tmp_path / "no-existe") == []


def test_has_previous_backup(tmp_path):
    assert not has_previous_backup(tmp_path, UDID)
    make_backup(tmp_path)
    assert has_previous_backup(tmp_path, UDID)


def test_space_needed_is_smaller_for_incremental():
    used = 287 * 10**9
    assert storage.space_needed(used, has_previous=True) < storage.space_needed(used, has_previous=False)
    assert storage.space_needed(used, has_previous=False) > used


def test_free_space_on_missing_folder(tmp_path):
    assert storage.free_space(tmp_path / "a" / "b") > 0


def test_directory_size(tmp_path):
    make_backup(tmp_path)
    assert storage.directory_size(tmp_path) > 10


# --- progreso ---

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_full_backup_progress_follows_bytes_not_device_jumps():
    clock = FakeClock()
    est = ProgressEstimator(expected_bytes=1000, clock=clock)
    est.update(0.0, 0)
    clock.t = 10
    percent, speed, eta = est.update(24.9, 100)  # el iPhone salta al 24,9 % con solo el 10 % recibido
    assert percent == 10
    assert speed == 10
    assert eta == 90


def test_progress_never_goes_back_nor_reaches_100():
    clock = FakeClock()
    est = ProgressEstimator(expected_bytes=None, clock=clock)
    assert est.update(None, 0)[0] is None
    assert est.update(50.0, 10)[0] == 50
    assert est.update(40.0, 20)[0] == 50
    assert est.update(100.0, 30)[0] == 99


def test_progress_over_expected_size_stays_below_100():
    est = ProgressEstimator(expected_bytes=100, clock=FakeClock())
    assert est.update(None, 500)[0] == 99


# --- errores ---

def test_friendly_errors():
    assert friendly(ConnectionTerminatedError()) is DISCONNECTED
    assert "espacio" in friendly(NotEnoughDiskSpaceError("x")).title
    assert "Detalle técnico" in friendly(ValueError("raro")).hint


# --- dispositivo y energía (leyendo un /sys de mentira) ---

def test_iphone_on_usb(tmp_path):
    write_sysfs(tmp_path, "3-2", idVendor="05ac", product="FaceTime HD Camera (Built-in)")
    assert not device.iphone_on_usb(tmp_path)
    write_sysfs(tmp_path, "2-1.2", idVendor="05ac", product="iPhone")
    assert device.iphone_on_usb(tmp_path)


def test_on_battery(tmp_path):
    write_sysfs(tmp_path, "BAT0", type="Battery")
    write_sysfs(tmp_path, "ADP1", type="Mains", online="0")
    assert power.on_battery(tmp_path)
    (tmp_path / "ADP1" / "online").write_text("1\n")
    assert not power.on_battery(tmp_path)


def test_desktop_without_battery_is_not_on_battery(tmp_path):
    assert not power.on_battery(tmp_path)


# --- formato ---

def test_formatting():
    assert fmt.size(245_212_989_992) == "245,2 GB"
    assert fmt.duration(30) == "menos de un minuto"
    assert fmt.duration(2 * 3600 + 5 * 60) == "2 h 05 min"
    now = datetime(2026, 10, 25, 18, tzinfo=timezone.utc)
    assert fmt.ago(now - timedelta(days=3), now) == "hace 3 días"
    assert fmt.ago(None) == "nunca"


# --- ajustes ---

def test_settings_roundtrip(tmp_path):
    from movris_vault.core import settings

    assert settings.load(tmp_path).root == storage.DEFAULT_ROOT
    settings.save(settings.Settings(backup_root="/media/disco/copias"), tmp_path)
    assert settings.load(tmp_path).root == Path("/media/disco/copias")
    (tmp_path / "settings.json").write_text("{roto")
    assert settings.load(tmp_path).root == storage.DEFAULT_ROOT


# --- cifrado y copia apartada ---

def test_check_new_password():
    from movris_vault.core.encryption import check_new_password

    assert check_new_password("abc", "abc")
    assert check_new_password("secreta1", "secreta2")
    assert check_new_password("secreta1", "secreta1") is None


def test_encryption_change_forces_full_backup(tmp_path):
    from movris_vault.core.backup import encryption_changed, next_backup_is_incremental

    make_backup(tmp_path, encrypted=False)
    assert not encryption_changed(tmp_path, UDID, device_encrypted=False)
    assert next_backup_is_incremental(tmp_path, UDID, device_encrypted=False)
    assert encryption_changed(tmp_path, UDID, device_encrypted=True)
    assert not next_backup_is_incremental(tmp_path, UDID, device_encrypted=True)


def test_set_aside_and_put_back(tmp_path):
    original = make_backup(tmp_path)
    aside = storage.set_aside(original, "sin-cifrar")
    assert not original.exists()
    entries = storage.list_backups(tmp_path)
    assert [e.set_aside for e in entries] == [True]

    # una copia nueva a medias, que falla: vuelve la anterior
    original.mkdir()
    (original / "a-medias").write_text("x")
    storage.put_back(aside, original)
    assert not aside.exists()
    assert (original / "Manifest.db").exists() and not (original / "a-medias").exists()
    [entry] = storage.list_backups(tmp_path)
    assert not entry.set_aside
