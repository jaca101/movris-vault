"""La copia en sí: lanzarla, seguir su progreso y cancelarla."""

from __future__ import annotations

import asyncio
import collections
import enum
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from pymobiledevice3.services.mobilebackup2 import (
    INCREMENTAL_BACKUP_REQUIRED_FILES,
    NP_LOCAL_AUTH_DISMISSED,
    NP_LOCAL_AUTH_PRESENTED,
    NP_SYNC_CANCEL_REQUEST,
    Mobilebackup2Service,
)

from . import device, storage
from .errors import CANCELLED_ON_DEVICE, FriendlyError, friendly

# Una copia completa ocupa algo menos que lo usado en el iPhone (en las pruebas: 245 GB de 287 GB)
FULL_BACKUP_RATIO = 0.85
# Sin aviso de código ni datos en este tiempo, el iPhone se ha quedado colgado
CONNECT_STALL_SECONDS = 60
COPY_STALL_SECONDS = 300
SPEED_WINDOW_SECONDS = 20


class Phase(enum.Enum):
    CONNECTING = "connecting"
    WAITING_PASSCODE = "waiting_passcode"
    COPYING = "copying"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        return self in (Phase.DONE, Phase.CANCELLED, Phase.FAILED)


@dataclass(frozen=True)
class Snapshot:
    phase: Phase
    incremental: bool = False
    percent: Optional[float] = None   # 0-100; None mientras no se sepa
    received: int = 0                 # bytes recibidos en esta copia
    speed: Optional[float] = None     # bytes/s
    eta: Optional[float] = None       # segundos
    stalled: bool = False
    error: Optional[FriendlyError] = None
    hint: str = ""
    set_aside: Optional[Path] = None  # copia anterior apartada porque cambió el cifrado


class ProgressEstimator:
    """Convierte lo que manda el iPhone en una barra que avanza de forma creíble.

    En una copia completa el porcentaje del iPhone va a saltos (0,3 % → 24,9 % de golpe), así que
    usamos los bytes recibidos frente al tamaño esperado. En una incremental no sabemos cuánto
    va a llegar y solo queda el porcentaje del iPhone.
    """

    def __init__(self, expected_bytes: Optional[int], clock: Callable[[], float] = time.monotonic) -> None:
        self.expected = expected_bytes
        self._clock = clock
        self._samples: collections.deque[tuple[float, int]] = collections.deque()
        self._percent = 0.0

    def update(self, device_percent: Optional[float], received: int) -> tuple[Optional[float], Optional[float], Optional[float]]:
        now = self._clock()
        self._samples.append((now, received))
        while len(self._samples) > 2 and now - self._samples[0][0] > SPEED_WINDOW_SECONDS:
            self._samples.popleft()
        t0, b0 = self._samples[0]
        speed = (received - b0) / (now - t0) if now - t0 >= 1 else None

        if self.expected:
            candidate = 100 * received / self.expected
        elif device_percent is not None:
            candidate = device_percent
        else:
            return None, speed, None
        # Nunca hacia atrás, y el 100 % solo cuando el iPhone dice que ha terminado
        self._percent = max(self._percent, min(candidate, 99.0))

        eta = None
        if self.expected and speed:
            eta = max(self.expected - received, 0) / speed
        return self._percent, speed, eta


def has_previous_backup(root: Path, udid: str) -> bool:
    path = root / udid
    return all((path / f).is_file() and (path / f).stat().st_size > 0 for f in INCREMENTAL_BACKUP_REQUIRED_FILES)


def encryption_changed(root: Path, udid: str, device_encrypted: bool) -> bool:
    """La copia guardada no coincide con el cifrado del iPhone: la siguiente será completa."""
    previous = storage.find_backup(root, udid)
    return previous is not None and previous.complete and previous.encrypted != device_encrypted


def next_backup_is_incremental(root: Path, udid: str, device_encrypted: bool) -> bool:
    return has_previous_backup(root, udid) and not encryption_changed(root, udid, device_encrypted)


class _ObservedBackupService(Mobilebackup2Service):
    """El servicio de copia de pymobiledevice3, avisándonos de los bytes y avisos del iPhone."""

    def __init__(self, lockdown, job: Optional["BackupJob"]) -> None:
        super().__init__(lockdown)
        # pymobiledevice3 nombra el logger por el módulo de la clase: que siga siendo el suyo
        self.logger = logging.getLogger(Mobilebackup2Service.__module__)
        self._job = job

    def _log_backup_notification(self, event: dict) -> None:
        super()._log_backup_notification(event)
        if self._job:
            self._job._on_notification(event.get("Name"))

    @asynccontextmanager
    async def device_link(self, *args, **kwargs):
        async with super().device_link(*args, **kwargs) as dl:
            if self._job is None:
                yield dl
                return
            recvall = dl._recvall

            async def counting_recvall(size: int) -> bytes:
                data = await recvall(size)
                self._job._on_bytes(len(data))
                return data

            dl._recvall = counting_recvall
            yield dl


class BackupJob:
    """Una copia. `run()` se ejecuta en un bucle asyncio; `cancel()` se puede llamar desde cualquier hilo."""

    def __init__(
        self,
        root: Path,
        on_update: Callable[[Snapshot], None],
        udid: Optional[str] = None,
        full: bool = False,
    ) -> None:
        self.root = root
        self.udid = udid
        self.full = full
        self._on_update = on_update
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._cancel_requested = False
        self._cancelled_on_device = False
        self._snapshot = Snapshot(Phase.CONNECTING)
        self._estimator = ProgressEstimator(None)
        self._device_percent: Optional[float] = None
        self._received = 0
        self._phase_since = time.monotonic()
        self._last_data = time.monotonic()

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    def cancel(self) -> None:
        if self._loop is None or self._task is None:
            return
        self._loop.call_soon_threadsafe(self._request_cancel)

    def _request_cancel(self) -> None:
        if self._task and not self._task.done():
            self._cancel_requested = True
            self._task.cancel()

    async def run(self) -> Snapshot:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self.root.mkdir(parents=True, exist_ok=True)
        ticker = asyncio.create_task(self._tick())
        try:
            lockdown = await device.connect(self.udid)
            try:
                self.udid = lockdown.udid
                info = await device.read_info(lockdown)
                full = self.full
                if encryption_changed(self.root, self.udid, info.encrypted):
                    # El iPhone empezará de cero: la copia buena se aparta y vuelve a su sitio si esta falla
                    label = "sin-cifrar" if info.encrypted else "cifrada"
                    self._set(set_aside=storage.set_aside(self.root / self.udid, label))
                    full = True
                incremental = not full and has_previous_backup(self.root, self.udid)
                expected = None if incremental else int(info.used * FULL_BACKUP_RATIO)
                self._estimator = ProgressEstimator(expected)
                self._set(phase=Phase.CONNECTING, incremental=incremental)
                async with _ObservedBackupService(lockdown, self) as service:
                    await service.backup(
                        full=full, backup_directory=self.root, progress_callback=self._on_device_percent
                    )
            finally:
                await lockdown.close()
            entry = storage.find_backup(self.root, self.udid)
            if entry is None or not entry.complete:
                raise FriendlyError(
                    "La copia no ha terminado bien", "El iPhone dejó de enviar datos antes de acabar. Vuelve a intentarlo."
                )
            self._set(phase=Phase.DONE, percent=100.0, eta=0.0, stalled=False)
        except asyncio.CancelledError:
            if not self._cancel_requested:
                raise
            hint = ""
            if self._snapshot.phase in (Phase.CONNECTING, Phase.WAITING_PASSCODE):
                # Cancelar con el aviso del código abierto deja al iPhone esperando
                hint = "Si la próxima copia no arranca, desconecta y vuelve a conectar el cable."
            await self._put_back_set_aside()
            self._set(phase=Phase.CANCELLED, stalled=False, hint=hint)
        except Exception as exc:
            error = CANCELLED_ON_DEVICE if self._cancelled_on_device else friendly(exc)
            await self._put_back_set_aside()
            self._set(phase=Phase.FAILED, stalled=False, error=error)
        finally:
            ticker.cancel()
        return self._snapshot

    async def _put_back_set_aside(self) -> None:
        aside = self._snapshot.set_aside
        if aside is None or self.udid is None:
            return
        # Borrar la copia a medias puede tardar: fuera del bucle
        await asyncio.to_thread(storage.put_back, aside, self.root / self.udid)
        self._set(set_aside=None)

    # --- Llamadas desde pymobiledevice3 (en el hilo del bucle asyncio) ---

    def _on_notification(self, name: Optional[str]) -> None:
        if name == NP_LOCAL_AUTH_PRESENTED:
            self._set(phase=Phase.WAITING_PASSCODE)
        elif name == NP_LOCAL_AUTH_DISMISSED and self._snapshot.phase == Phase.WAITING_PASSCODE:
            self._set(phase=Phase.CONNECTING)
        elif name == NP_SYNC_CANCEL_REQUEST:
            self._cancelled_on_device = True

    def _on_device_percent(self, percent: float) -> None:
        self._device_percent = float(percent)
        self._last_data = time.monotonic()
        # El iPhone llama esto por cada fichero; la pantalla se refresca una vez por segundo en _tick
        if self._snapshot.phase != Phase.COPYING:
            self._set(phase=Phase.COPYING)

    def _on_bytes(self, n: int) -> None:
        self._received += n
        self._last_data = time.monotonic()

    # --- Estado ---

    def _refresh(self) -> None:
        percent, speed, eta = self._estimator.update(self._device_percent, self._received)
        self._set(percent=percent, speed=speed, eta=eta, received=self._received)

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(1)
            phase = self._snapshot.phase
            if phase == Phase.COPYING:
                self._refresh()
            now = time.monotonic()
            stalled = (phase == Phase.CONNECTING and now - self._phase_since > CONNECT_STALL_SECONDS) or (
                phase == Phase.COPYING and now - self._last_data > COPY_STALL_SECONDS
            )
            if stalled != self._snapshot.stalled:
                self._set(stalled=stalled)

    def _set(self, **changes) -> None:
        if "phase" in changes and changes["phase"] != self._snapshot.phase:
            self._phase_since = time.monotonic()
        self._snapshot = replace(self._snapshot, **changes)
        self._on_update(self._snapshot)
