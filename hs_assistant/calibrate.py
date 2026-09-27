"""Screenshot the Hearthstone window with every computed click position marked.

    uv run python -m hs_assistant.calibrate
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw, ImageFont, ImageGrab

from .actions import Executor, ScreenMapper
from .config import CACHE_DIR, DEFAULT_HS_DIR, Layout
from .log_reader import PowerLogTailer
from .mouse import virtual_screen
from .power_parser import PowerLogParser
from .screen import client_rect, enable_dpi_awareness, find_hearthstone_window

OUTPUT = CACHE_DIR / "calibration.png"
PREVIEW_WIDTH = 1920


def collect_points(layout: Layout, m: ScreenMapper) -> list[tuple[str, tuple[int, int], str]]:
    points: list[tuple[str, tuple[int, int], str]] = []
    for name in ("end_turn", "board_center", "my_hero", "my_hero_power", "my_weapon",
                 "opp_hero", "opp_hero_power", "opp_weapon", "mulligan_confirm"):
        points.append((name, m.at(getattr(layout, name)), "yellow"))

    parser = PowerLogParser()
    for line in PowerLogTailer(logs_dir=DEFAULT_HS_DIR / "Logs").poll():
        parser.feed(line)
    s = parser.state
    me, opp = s.friendly_player_id, s.opponent_player_id
    hand = s.hand(me)
    for i in range(len(hand)):
        points.append((f"hand{i + 1}", m.hand_card(i, len(hand)), "lime"))
    for pid, color, friendly in ((me, "cyan", True), (opp, "red", False)):
        board = s.board(pid)
        for i in range(len(board)):
            points.append((f"{'my' if friendly else 'opp'}{i + 1}", m.board_slot(i, len(board), friendly), color))
    mine = len(s.board(me))
    points.append(("drop", m.board_slot(mine, mine + 1, True), "magenta"))
    return points


def main() -> Path:
    enable_dpi_awareness()
    hwnd = find_hearthstone_window()
    if not hwnd:
        raise SystemExit("找不到炉石传说窗口")
    rect = client_rect(hwnd)
    layout = Layout.load()
    m = ScreenMapper(layout, rect)
    points = collect_points(layout, m)

    vx, vy, _, _ = virtual_screen()
    shot = ImageGrab.grab(all_screens=True)
    img = shot.crop((rect.left - vx, rect.top - vy, rect.left - vx + rect.width, rect.top - vy + rect.height))
    draw = ImageDraw.Draw(img)
    radius = max(6, rect.height // 120)
    font = ImageFont.load_default(size=max(14, rect.height // 50))
    for label, (x, y), color in points:
        cx, cy = x - rect.left, y - rect.top
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=4)
        draw.line((cx - radius * 2, cy, cx + radius * 2, cy), fill=color, width=2)
        draw.line((cx, cy - radius * 2, cx, cy + radius * 2), fill=color, width=2)
        draw.text((cx + radius + 4, cy - radius * 2), label, fill=color, font=font, stroke_width=2, stroke_fill="black")

    if img.width > PREVIEW_WIDTH:
        img = img.resize((PREVIEW_WIDTH, round(img.height * PREVIEW_WIDTH / img.width)))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT)
    print(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    main()
