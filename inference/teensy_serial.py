from __future__ import annotations
import time
import re
import os
import glob
from typing import Callable, Optional, Dict, Any, List, Tuple

import serial
from serial.tools import list_ports

from app_paths import raw_data_dir
from PyQt6.QtCore import QObject, pyqtSignal, QTimer


NEWLINE = b"\n"


def available_port_infos() -> list[dict]:
    """Scans the system for available serial ports and returns their metadata."""
    infos: list[dict] = []
    for p in list_ports.comports():
        infos.append({
            "device": p.device,
            "description": (p.description or ""),
            "hwid": (p.hwid or ""),
            "vid": getattr(p, "vid", None),
            "pid": getattr(p, "pid", None),
        })
    return infos


def pick_default_port(prefer_keywords: list[str] | None = None) -> Optional[str]:
    """Automatically selects the best port based on known keywords like Teensy."""
    prefer_keywords = prefer_keywords or ["teensy", "usb serial", "cdc", "acm", "serial"]
    infos = available_port_infos()
    if not infos: return None

    def score(info: dict) -> int:
        text = f"{info.get('description', '')} {info.get('hwid', '')}".lower()
        s = 0
        for k in prefer_keywords:
            if k.lower() in text: s += 10
        if info.get("vid") is not None: s += 1
        return s

    infos_sorted = sorted(infos, key=score, reverse=True)
    return infos_sorted[0].get("device")


class TeensySerialSource(QObject):
    """
    Reads data from Teensy via Serial and maps short IDs to long Sensor IDs.
    """
    status = pyqtSignal(str)

    def __init__(self, callback: Callable[[dict], None], port: str, baud: int = 115200):
        super().__init__()
        self.callback = callback
        self.port = port
        self.baud = int(baud)

        self._ser: Optional[serial.Serial] = None
        self._timer = QTimer()
        self._timer.setInterval(10)  # 100Hz poll
        self._timer.timeout.connect(self._poll)

        # --- ID MAPPING (Short ID -> Long ID) ---
        self.sensor_mapping = self._build_sensor_mapping()

        self._last_rx_ts = 0.0
        # Bytes read that did not end in a newline yet. The port is opened
        # non-blocking, so a poll routinely lands in the middle of a line:
        # keep the fragment and finish it with the next read.
        self._buf = b""
        # Counted AND reported: a line the parser cannot use says the link or
        # the firmware is misbehaving, and silence about it once cost us wrong
        # readings rather than missing ones. Reported at most every few
        # seconds so a bad cable cannot flood the status bar.
        self.bad_lines = 0
        self._bad_reported = 0
        self._bad_report_ts = 0.0

    def _build_sensor_mapping(self) -> Dict[str, str]:
        """
        Scans 'input_raw_data' folder and maps short codes (A1, B2) to full filenames.
        Example: 'A1' -> '2026-02-02-05-14-01_C_Z1p8_H10_C5_R20_S1_MatG_A1'
        """
        mapping = {}
        raw_data_path = os.path.join(raw_data_dir(), "*.csv")

        files = glob.glob(raw_data_path)
        for f in files:
            full_name = os.path.splitext(os.path.basename(f))[0]
            # Extract last 2 characters (A1, B2 etc.) from filename
            short_id = full_name.split('_')[-1]
            mapping[short_id] = full_name

        print(f"DEBUG: {len(mapping)} sensor mappings created.")
        return mapping

    def start(self) -> None:
        """Initializes the serial connection and starts polling."""
        if self._ser is not None: return
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0)
            self._ser.reset_input_buffer()
            self._buf = b""
            self._last_rx_ts = time.time()
            self.status.emit(f"Connected to {self.port}")
            self._timer.start()
        except Exception as e:
            self.status.emit(f"Error: {e}")

    def stop(self) -> None:
        """Stops the timer and closes the serial port."""
        self._timer.stop()
        if self._ser: self._ser.close()
        self._ser = None
        self._buf = b""
        self.status.emit("Disconnected")

    def _poll(self) -> None:
        """Drain whatever the port has, and hand on the complete lines.

        The port is non-blocking, so a read lands wherever the Teensy happens
        to be: at 115200 baud a line takes about 1.7 ms to arrive and the poll
        runs every 10 ms, so catching one half-sent is routine.

        readline() used to be called directly, which returned that half. The
        fragment still had four comma-separated fields often enough to parse -
        "A1,12,34,56" out of "A1,12,34,5603.0" reads as 56.0 - so a truncated
        line did not leave a gap in the data, it wrote a plausible wrong
        number into it. Everything up to the last newline is used here and the
        remainder is carried over to the next poll instead.
        """
        if self._ser is None: return

        try:
            waiting = self._ser.in_waiting
            if waiting:
                self._buf += self._ser.read(waiting)
        except Exception as e:
            self.status.emit(f"Read error: {e}")
            return

        if NEWLINE not in self._buf:
            return

        # Anything after the final newline is an unfinished line; hold it back.
        chunk, _, self._buf = self._buf.rpartition(NEWLINE)
        self._last_rx_ts = time.time()

        for line_bytes in chunk.split(NEWLINE):
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            if not line or line.startswith("Sensor_Id"): continue

            parts = line.split(',')
            if len(parts) < 4:
                self.bad_lines += 1
                continue
            try:
                short_id = parts[0].strip().replace("-", "")
                raw_bz = float(parts[3])
            except ValueError:
                self.bad_lines += 1
                continue

            long_id = self.sensor_mapping.get(short_id, short_id)
            self.callback({
                "t": time.time(),
                "sensor_id": long_id,
                "short_id": short_id,
                "raw_bz": raw_bz,
            })

        self._report_bad_lines()

    def _report_bad_lines(self, every_s: float = 5.0) -> None:
        """Say so when lines are being thrown away, without flooding."""
        if self.bad_lines == self._bad_reported:
            return
        now = time.time()
        if now - self._bad_report_ts < every_s:
            return
        new = self.bad_lines - self._bad_reported
        self._bad_reported = self.bad_lines
        self._bad_report_ts = now
        self.status.emit(f"{new} unreadable line(s) from {self.port} "
                         f"({self.bad_lines} so far) - check the cable and the "
                         f"baud rate")