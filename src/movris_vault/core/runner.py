"""Un bucle asyncio en su propio hilo, para que la ventana no se congele mientras hablamos con el iPhone."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Coroutine


class AsyncRunner:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="movris-vault-async", daemon=True)
        self._thread.start()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
