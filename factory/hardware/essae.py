"""Essae DS-215 platform scale over RS232.

Factory wiring: Essae DS-215 indicator -> RS232 -> factory PC COM port.
Defaults: 9600 baud, 8 data bits, No parity, 1 stop (8N1) — the Essae
factory default. If the indicator was reconfigured, set it in the
Hardware dialog (saved to data/hardware.json).

Protocol: Essae indicators stream plain-ASCII weight lines (continuous
or on-demand depending on indicator setup). Exact framing varies by
firmware (STX/+/-, "kg", stable-flag), so we don't require one format:
we grab the last float on each line and only accept it when the line
looks stable. This survives " 12.340 kg", "+12.340", "ST,GS,+ 12.340kg",
etc. Unstable/motion lines (containing "UN", "MOT", "OL") are ignored.

No pyserial on the PC? Import fails -> driver reports unavailable and
the UI falls back to manual entry (mock). Fully offline-safe.
"""
from __future__ import annotations
import re
import time

from .base import ScaleInterface

_FLOAT = re.compile(r"[-+]?\d+\.\d+|\d+")
_UNSTABLE = re.compile(r"\b(UNSTABLE|UNST|UN|MOTION|MOT|OL|OVER|ERR)\b", re.I)


def parse_weight_line(line: str) -> float | None:
    """Best-effort Essae line -> kg. None = not a stable reading."""
    if not line:
        return None
    t = line.strip().upper()
    if not t:
        return None
    if _UNSTABLE.search(t):
        return None
    nums = _FLOAT.findall(t)
    if not nums:
        return None
    try:
        v = float(nums[-1])
    except ValueError:
        return None
    if v < 0 or v > 500:
        return None
    return round(v, 3)


def list_ports() -> list[str]:
    """COM/TTY names present on this PC. [] when pyserial missing."""
    try:
        from serial.tools import list_ports as lp
    except Exception:
        return []
    try:
        return [p.device for p in lp.comports()]
    except Exception:
        return []


class EssaeDS215(ScaleInterface):
    """Real Essae DS-215. port e.g. 'COM3' (Windows) or '/dev/ttyS0' (Linux)."""

    def __init__(self, port: str, baud: int = 9600, timeout: float = 1.0):
        if not port:
            raise ValueError("Scale port not configured (set COM port in Hardware settings)")
        self.port = port
        self.baud = int(baud or 9600)
        self.timeout = timeout
        self._ser = None
        self._last: float = 0.0
        self._frozen: float | None = None

    def _open(self):
        if self._ser is not None:
            return self._ser
        try:
            import serial
        except ImportError as e:
            raise RuntimeError(
                "pyserial not installed — run: pip install pyserial") from e
        self._ser = serial.Serial(
            port=self.port, baudrate=self.baud,
            bytesize=8, parity="N", stopbits=1,
            timeout=self.timeout, write_timeout=1.0)
        return self._ser

    def close(self):
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    def _read_lines(self, dwell: float = 0.8) -> list[str]:
        ser = self._open()
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        end = time.time() + dwell
        lines: list[str] = []
        buf = b""
        while time.time() < end:
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except Exception:
                break
            if chunk:
                buf += chunk
                while b"\n" in buf or b"\r" in buf:
                    for sep in (b"\r\n", b"\n", b"\r"):
                        if sep in buf:
                            raw, buf = buf.split(sep, 1)
                            try:
                                lines.append(raw.decode("ascii", "ignore"))
                            except Exception:
                                pass
                            break
            else:
                time.sleep(0.05)
        # trailing fragment without newline (on-demand indicators)
        if buf.strip():
            try:
                lines.append(buf.decode("ascii", "ignore"))
            except Exception:
                pass
        return lines

    def read_weight(self) -> float:
        """Fresh measured weight in kg. Raises on failure — never stale/zero.

        Every call samples the indicator anew. Timeout (no lines), unstable
        / motion-only lines, disconnects and serial errors all raise
        RuntimeError so the UI can show an error instead of recording a
        stale or 0.000 kg reading as a real measurement. A frozen value
        (see freeze()) is the only non-fresh return, and only until
        unfreeze() clears it.
        """
        if self._frozen is not None:
            return self._frozen
        lines = self._read_lines()
        for line in reversed(lines):
            v = parse_weight_line(line)
            if v is not None:
                self._last = v
                return v
        if not lines:
            raise RuntimeError(
                f"No data from scale on {self.port} — check cable, power and COM port")
        raise RuntimeError(
            "No stable weight — scale is moving/unstable, wait and read again")

    def freeze(self) -> float:
        v = self.read_weight()  # raises when nothing fresh: never freezes stale
        self._frozen = v
        # freeze holds until unfreeze() clears it
        return v

    def unfreeze(self):
        self._frozen = None

    def test(self) -> dict:
        """Open port, grab a reading. Returns {ok, port, lines, weight, error}."""
        try:
            lines = self._read_lines(dwell=1.2)
            vals = [(ln, parse_weight_line(ln)) for ln in lines[-8:]]
            good = [v for _, v in vals if v is not None]
            return {"ok": bool(good), "port": self.port,
                    "lines": [ln for ln, _ in vals],
                    "weight": good[-1] if good else 0.0,
                    "error": "" if good else "no stable weight lines in ~1s — check cable/baud/COM"}
        except Exception as e:
            return {"ok": False, "port": self.port, "lines": [],
                    "weight": 0.0, "error": str(e)}
