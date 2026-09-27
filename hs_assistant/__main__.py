from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path

from .config import DEFAULT_HS_DIR, LOG_CONFIG_PATH, Layout
from .screen import enable_dpi_awareness


def main() -> None:
    ap = argparse.ArgumentParser(prog="hs_assistant", description=__doc__)
    ap.add_argument("--hs-dir", type=Path, default=DEFAULT_HS_DIR, help="Hearthstone install folder")
    ap.add_argument("--log", type=Path, help="Read this Power.log instead of the newest one")
    ap.add_argument("--setup", action="store_true", help="Enable Power.log in log.config and exit")
    args = ap.parse_args()

    from .log_setup import enable_power_log

    if args.setup:
        changed = enable_power_log()
        print(f"{LOG_CONFIG_PATH}: {'updated - restart Hearthstone' if changed else 'already enabled'}")
        return

    enable_dpi_awareness()

    from .actions import Executor
    from .cards import CardDatabase
    from .gui import App
    from .log_reader import PowerLogTailer

    cards = CardDatabase()
    cards.load_async()
    tailer = PowerLogTailer(logs_dir=args.hs_dir / "Logs", fixed_file=args.log)
    root = tk.Tk()
    App(root, tailer, cards, Executor(Layout.load()))
    root.mainloop()


if __name__ == "__main__":
    main()
