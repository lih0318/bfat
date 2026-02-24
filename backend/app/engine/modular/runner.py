"""
Modular engine runner: signal -> risk -> execution -> position management -> performance logging -> optimizer (periodic).
No global shared state. No Binance calls — all via execution_engine.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from app.engine.modular.config_model import ModularConfig, load_modular_config
from app.engine.modular.orchestrator import tick
from app.engine.modular.types import SignalResult

logger = logging.getLogger(__name__)


class ModularRunner:
    """Orchestrates: signal -> risk -> execution -> pos_mgmt -> perf_log -> optimizer (periodic)."""

    def __init__(self) -> None:
        self._config: ModularConfig = load_modular_config()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status_reason: str = "ok"
        self._last_status: dict[str, Any] = {}
        self._optimizer_state = None
        self._peak_equity: float = 0.0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def config(self) -> ModularConfig:
        return self._config

    @config.setter
    def config(self, cfg: ModularConfig) -> None:
        self._config = cfg

    def get_status(self) -> dict[str, Any]:
        out = {
            "running": self._running,
            "reason": self._status_reason,
            "symbols_count": self._last_status.get("symbols_count", 0),
            "targets_count": self._last_status.get("decisions_allowed", 0),
            "equity": self._last_status.get("equity", 0.0),
            "peak_equity": self._last_status.get("peak_equity", 0.0),
        }
        return out

    def _loop(self) -> None:
        interval = self._config.execution_tick_sec
        while not self._stop_event.is_set():
            status, self._optimizer_state, self._peak_equity = tick(
                self._config, self._optimizer_state, self._peak_equity,
            )
            self._status_reason = status.get("reason", "ok")
            self._last_status = {
                "reason": self._status_reason,
                "symbols_count": status.get("symbols_count", 0),
                "decisions_allowed": status.get("decisions_allowed", 0),
                "equity": status.get("equity", 0.0),
                "peak_equity": status.get("peak_equity", self._peak_equity),
                "success": status.get("success"),
            }
            if self._stop_event.wait(timeout=interval):
                break

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._config = load_modular_config()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("ModularRunner started")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("ModularRunner stopped")
