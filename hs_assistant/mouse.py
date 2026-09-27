"""Mouse control via Win32 SendInput, working on every monitor of the virtual desktop."""

from __future__ import annotations

import ctypes
import math
import threading
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

STEP_SECONDS = 0.01
USER_MOVE_TOLERANCE = 40


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        # KEYBDINPUT/HARDWAREINPUT are smaller than MOUSEINPUT, so this union
        # already has the size SendInput expects.
        _fields_ = [("mi", MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class MouseAborted(RuntimeError):
    """Raised when the user moves the mouse while an action is running."""


def position() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def virtual_screen() -> tuple[int, int, int, int]:
    gsm = user32.GetSystemMetrics
    return gsm(SM_XVIRTUALSCREEN), gsm(SM_YVIRTUALSCREEN), gsm(SM_CXVIRTUALSCREEN), gsm(SM_CYVIRTUALSCREEN)


def _send(flags: int, dx: int = 0, dy: int = 0) -> None:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Mouse:
    def __init__(self, abort: threading.Event | None = None) -> None:
        self._expected: tuple[int, int] | None = None
        self.abort = abort or threading.Event()

    def _check_user(self) -> None:
        if self.abort.is_set():
            raise MouseAborted("已停止")
        if self._expected is None:
            return
        x, y = position()
        ex, ey = self._expected
        if math.hypot(x - ex, y - ey) > USER_MOVE_TOLERANCE:
            self._expected = None
            raise MouseAborted("检测到鼠标被移动，操作已中止")

    def _set(self, x: int, y: int) -> None:
        left, top, width, height = virtual_screen()
        nx = round((x - left) * 65535 / max(width - 1, 1))
        ny = round((y - top) * 65535 / max(height - 1, 1))
        _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)
        self._expected = (x, y)

    def begin(self) -> None:
        self._expected = None

    def move(self, x: int, y: int, duration: float = 0.2) -> None:
        self._check_user()
        sx, sy = position()
        steps = max(1, int(duration / STEP_SECONDS))
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)
            self._set(round(sx + (x - sx) * t), round(sy + (y - sy) * t))
            time.sleep(STEP_SECONDS)
            self._check_user()

    def down(self) -> None:
        self._check_user()
        _send(MOUSEEVENTF_LEFTDOWN)

    def up(self) -> None:
        _send(MOUSEEVENTF_LEFTUP)

    def click(self) -> None:
        self.down()
        time.sleep(0.05)
        self.up()
