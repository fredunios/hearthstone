from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_DIR / "cache"
LAYOUT_PATH = PROJECT_DIR / "layout.json"

DEFAULT_HS_DIR = Path(os.environ.get("HEARTHSTONE_DIR", r"E:\Hearthstone"))
LOG_CONFIG_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "Blizzard" / "Hearthstone" / "log.config"

Point = tuple[float, float]


@dataclass
class Layout:
    """Screen positions inside the Hearthstone window.

    Every value is a fraction of the window's client height. ``x`` values are
    offsets from the horizontal centre, ``y`` values are measured from the top.
    Hearthstone scales the board with the window height and pads the sides on
    wide screens, so height is the unit on both axes.
    """

    end_turn: Point = (0.556, 0.455)
    board_center: Point = (0.0, 0.47)

    my_hero: Point = (0.0, 0.765)
    my_hero_power: Point = (0.165, 0.765)
    my_weapon: Point = (-0.165, 0.775)
    opp_hero: Point = (0.0, 0.175)
    opp_hero_power: Point = (0.16, 0.217)
    opp_weapon: Point = (-0.16, 0.217)

    my_board_y: float = 0.565
    opp_board_y: float = 0.37
    minion_spacing: float = 0.13

    hand_center_x: float = -0.04
    hand_y: float = 0.93
    hand_max_spacing: float = 0.115
    hand_width: float = 0.5

    choice_y: float = 0.5
    choice_max_spacing: float = 0.3
    choice_width: float = 0.92
    mulligan_y: float = 0.47
    mulligan_confirm: Point = (0.0, 0.83)

    @classmethod
    def load(cls) -> Layout:
        layout = cls()
        if LAYOUT_PATH.exists():
            data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
            for f in fields(cls):
                if f.name in data:
                    value = data[f.name]
                    setattr(layout, f.name, tuple(value) if isinstance(value, list) else value)
        else:
            layout.save()
        return layout

    def save(self) -> None:
        LAYOUT_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
