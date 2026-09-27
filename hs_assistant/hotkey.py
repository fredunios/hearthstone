"""Global hotkey that works even while Hearthstone has focus."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from ctypes import wintypes

user32 = ctypes.windll.user32

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_F9 = 0x78


def start_hotkey_listener(callback: Callable[[], None], vk: int = VK_F9) -> threading.Event:
    """Call ``callback`` from a background thread whenever ``vk`` is pressed.

    Returns an event that is set once registration has been attempted; the
    ``ok`` attribute on it tells whether the hotkey could be registered.
    """
    ready = threading.Event()
    ready.ok = False  # type: ignore[attr-defined]

    def run() -> None:
        # RegisterHotKey with no window posts WM_HOTKEY to this thread's queue.
        ready.ok = bool(user32.RegisterHotKey(None, 1, MOD_NOREPEAT, vk))  # type: ignore[attr-defined]
        ready.set()
        if not ready.ok:  # type: ignore[attr-defined]
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                callback()

    threading.Thread(target=run, daemon=True, name="hotkey").start()
    return ready
