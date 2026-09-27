from __future__ import annotations

import time
from pathlib import Path

CREATE_GAME_MARKER = b"GameState.DebugPrintPower() - CREATE_GAME"


def find_latest_power_log(logs_dir: Path) -> Path | None:
    candidates = list(logs_dir.glob("*/Power.log"))
    root_log = logs_dir / "Power.log"
    if root_log.exists():
        candidates.append(root_log)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


class PowerLogTailer:
    """Follows the newest Power.log and returns complete new lines.

    When a file is first opened, reading starts at the last CREATE_GAME so an
    assistant started mid-game picks up the current match without replaying
    every earlier one.
    """

    RESCAN_SECONDS = 2.0

    def __init__(self, logs_dir: Path | None = None, fixed_file: Path | None = None) -> None:
        self.logs_dir = logs_dir
        self.fixed_file = fixed_file
        self.path: Path | None = None
        self._offset = 0
        self._partial = b""
        self._last_scan = 0.0

    def _pick_file(self) -> Path | None:
        if self.fixed_file is not None:
            return self.fixed_file
        now = time.monotonic()
        if self.path is not None and now - self._last_scan < self.RESCAN_SECONDS:
            return self.path
        self._last_scan = now
        if self.logs_dir is None or not self.logs_dir.exists():
            return None
        return find_latest_power_log(self.logs_dir)

    def _open_new(self, path: Path) -> bytes:
        self.path = path
        self._partial = b""
        data = path.read_bytes()
        start = data.rfind(CREATE_GAME_MARKER)
        start = 0 if start < 0 else data.rfind(b"\n", 0, start) + 1
        self._offset = len(data)
        return data[start:]

    def poll(self) -> list[str]:
        path = self._pick_file()
        if path is None or not path.exists():
            return []
        if path != self.path:
            chunk = self._open_new(path)
        else:
            size = path.stat().st_size
            if size < self._offset:
                chunk = self._open_new(path)
            elif size == self._offset:
                return []
            else:
                with path.open("rb") as fh:
                    fh.seek(self._offset)
                    chunk = fh.read(size - self._offset)
                self._offset = size
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()
        return [line.decode("utf-8", errors="replace") for line in lines]
