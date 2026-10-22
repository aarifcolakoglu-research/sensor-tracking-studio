from __future__ import annotations

import time
import math
import random
from typing import Callable, Dict, Tuple, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QTimer


class SimulatedSensorSource(QObject):
    """
    Generates realistic fake data for all active sensors (no hardware needed).

    Strategy:
      1. For each sensor, pick a distinct force waveform f(t) in Newtons.
      2. INVERT the sensor's own polynomial (fz = a*x^2 + b*x + c) to find
         the raw_bz value that would produce that force.
      3. Emit events in the exact same format as TeensySerialSource, so
         main.py -> PolynomialEngine -> GUI pipeline works untouched.
    """

    status = pyqtSignal(str)

    # Distinct waveform styles cycled across sensors
    PATTERNS = [
        "ramp_hold", "bump", "slow_rise", "steps",
        "plateau", "sine", "osc_fast", "decay",
        "double_bump", "sawtooth", "ramp_hold", "sine",
    ]

    def __init__(self, callback: Callable[[dict], None], engine,
                 interval_ms: int = 40, bindings: Dict[str, str] = None):
        super().__init__()
        self.callback = callback
        self.engine = engine
        # socket -> sensor_id, the same mapping the dashboard uses. Simulating
        # the bound sockets rather than every loaded model matters now that
        # there are 37 models for 12 sockets: three of them are called "A1".
        self.bindings = dict(bindings or {})

        self._timer = QTimer()
        self._timer.setInterval(int(interval_ms))  # 40ms -> 25 Hz per sensor
        self._timer.timeout.connect(self._tick)

        self._t0: float = 0.0
        self._sensor_cfg: Dict[str, dict] = {}
        self._noise_state: Dict[str, float] = {}  # low-pass filtered drift per sensor
        self._build_sensor_configs()

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #
    def _build_sensor_configs(self) -> None:
        """Assign a waveform, phase offset and zero-reference raw value per socket."""
        if not self.bindings:
            print("[Sim]: No sockets bound, nothing to simulate.")
            return

        for i, position in enumerate(sorted(self.bindings)):
            long_id = self.bindings[position]
            if self.engine.curve(long_id) is None:
                print(f"[Sim]: {position} -> '{long_id}' has no model, skipped.")
                continue

            pattern = self.PATTERNS[i % len(self.PATTERNS)]

            # Raw value corresponding to ~0 N. The engine inverts whatever
            # degree is loaded, by bisection inside the calibrated range, so
            # this no longer depends on the polynomial being solvable by hand.
            ref_raw = self.engine.invert(long_id, 0.0, "Loading")

            self._sensor_cfg[position] = {
                "long_id": long_id,
                "short_id": position,
                "pattern": pattern,
                "phase": random.uniform(0.0, 6.28),
                "speed": random.uniform(0.85, 1.15),
                "ref_raw": ref_raw,
            }

        print(f"[Sim]: Configured {len(self._sensor_cfg)} simulated sockets.")

    # ------------------------------------------------------------------ #
    # Waveforms (force in Newtons, roughly 0..1 N range)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _waveform(pattern: str, t: float) -> float:
        # Each pattern loops over a 20 second period
        p = (t % 20.0) / 20.0

        if pattern == "ramp_hold":
            f = min(p * 2.5, 1.0) * 0.65
        elif pattern == "bump":
            f = 0.80 * math.exp(-((p - 0.5) ** 2) / 0.02) + 0.10
        elif pattern == "slow_rise":
            f = 0.10 + 0.70 * (p ** 1.5)
        elif pattern == "steps":
            f = 0.15 + 0.25 * (p > 0.33) + 0.30 * (p > 0.66)
        elif pattern == "plateau":
            f = min(p * 4.0, 1.0) * 0.55 + 0.05
        elif pattern == "sine":
            f = 0.45 + 0.30 * math.sin(2 * math.pi * p)
        elif pattern == "osc_fast":
            f = 0.45 + 0.18 * math.sin(2 * math.pi * p * 3.0)
        elif pattern == "decay":
            f = 0.85 * math.exp(-3.0 * p) + 0.10
        elif pattern == "double_bump":
            f = (0.6 * math.exp(-((p - 0.3) ** 2) / 0.01)
                 + 0.8 * math.exp(-((p - 0.7) ** 2) / 0.01) + 0.08)
        elif pattern == "sawtooth":
            f = 0.15 + 0.6 * (p * 3.0 % 1.0)
        else:
            f = 0.3

        return max(0.0, f)

    # ------------------------------------------------------------------ #
    # Lifecycle (same interface as TeensySerialSource)
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._t0 = time.time()
        self._timer.start()
        self.status.emit("Simulation started")

    def stop(self) -> None:
        self._timer.stop()
        self.status.emit("Simulation stopped")

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        now = time.time()
        t = now - self._t0

        for position, cfg in self._sensor_cfg.items():
            long_id = cfg["long_id"]

            # 1. Desired force + realistic sensor noise
            force = self._waveform(cfg["pattern"], t * cfg["speed"] + cfg["phase"])

            # Low-pass filtered noise: slow drift instead of jitter.
            # 0.97  -> how slowly the drift returns (0.99 = even slower, longer waves)
            # 0.0015 -> how far the drift wanders
            n_prev = self._noise_state.get(position, 0.0)
            n = 0.97 * n_prev + random.gauss(0.0, 0.0015)
            self._noise_state[position] = n

            force += n + random.gauss(0.0, 0.005)  # drift + tiny measurement noise
            force = max(0.0, force)

            # 2. Convert force to the raw_bz that produces it. Always from the
            #    loading curve: the engine may convert it back with another,
            #    which is the point of the phase modes and should stay visible
            #    rather than cancel out.
            raw_bz = self.engine.invert(long_id, force, "Loading")
            if raw_bz is None:
                continue

            # 3. Emit event identical to the serial source format
            evt = {
                "t": now,
                "sensor_id": long_id,
                "short_id": cfg["short_id"],
                "raw_bz": raw_bz,
            }
            self.callback(evt)