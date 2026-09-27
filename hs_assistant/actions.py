from __future__ import annotations

import threading

from .config import Layout
from .game_state import BOARD_CARD_TYPES, Choice, GameState, Option
from .mouse import Mouse, MouseAborted
from .screen import WindowRect, client_rect, find_hearthstone_window, focus_window

ANIMATION_WAIT = 0.9
START_DELAY = 1.0  # time for the user to let go of the mouse after clicking a button

ScreenPoint = tuple[int, int]


class ActionError(RuntimeError):
    pass


def _spread(index: int, count: int, max_spacing: float, width: float) -> float:
    """Horizontal offset of item ``index`` (0-based) in a centred row."""
    spacing = min(max_spacing, width / max(count, 1))
    return (index - (count - 1) / 2) * spacing


class ScreenMapper:
    def __init__(self, layout: Layout, rect: WindowRect) -> None:
        self.layout = layout
        self.rect = rect

    def at(self, point: tuple[float, float]) -> ScreenPoint:
        return self.rect.to_screen(*point)

    def hand_card(self, index: int, count: int) -> ScreenPoint:
        lay = self.layout
        dx = lay.hand_center_x + _spread(index, count, lay.hand_max_spacing, lay.hand_width)
        return self.rect.to_screen(dx, lay.hand_y)

    def board_slot(self, index: int, count: int, friendly: bool) -> ScreenPoint:
        lay = self.layout
        dx = (index - (count - 1) / 2) * lay.minion_spacing
        return self.rect.to_screen(dx, lay.my_board_y if friendly else lay.opp_board_y)

    def choice_card(self, index: int, count: int, y: float) -> ScreenPoint:
        lay = self.layout
        return self.rect.to_screen(_spread(index, count, lay.choice_max_spacing, lay.choice_width), y)


class Executor:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.dry_run = False
        self.start_delay = START_DELAY
        self.abort = threading.Event()
        self.mouse = Mouse(self.abort)

    # ---- mouse primitives --------------------------------------------------

    def _wait(self, seconds: float) -> None:
        if self.abort.wait(seconds):
            raise MouseAborted("已停止")

    def _move(self, p: ScreenPoint) -> None:
        self.mouse.move(*p, duration=0.2)

    def _click(self, p: ScreenPoint) -> None:
        self._move(p)
        self._wait(0.2)
        if self.dry_run:
            self._wait(0.5)
            return
        self.mouse.click()

    def _drag(self, start: ScreenPoint, end: ScreenPoint) -> None:
        # Hearthstone only picks a card up after it has been hovered for a
        # moment, and the dragged card trails the cursor, so releasing too soon
        # cancels the play.
        self.mouse.move(*start, duration=0.3)
        self._wait(0.4)
        if self.dry_run:
            self._move(end)
            self._wait(0.5)
            return
        self.mouse.down()
        try:
            self._wait(0.15)
            self.mouse.move(*end, duration=0.5)
            self._wait(0.4)
        finally:
            self.mouse.up()

    def _prepare(self) -> ScreenMapper:
        self._wait(self.start_delay)
        self.mouse.begin()
        hwnd = find_hearthstone_window()
        if not hwnd:
            raise ActionError("找不到炉石传说窗口")
        focus_window(hwnd)
        self._wait(0.15)
        rect = client_rect(hwnd)
        if rect is None or rect.height == 0:
            raise ActionError("无法读取炉石传说窗口大小")
        return ScreenMapper(self.layout, rect)

    # ---- locating entities -------------------------------------------------

    def entity_point(
        self, m: ScreenMapper, state: GameState, entity_id: int, extra_friendly_minion: bool = False
    ) -> ScreenPoint:
        ent = state.entities.get(entity_id)
        if ent is None:
            raise ActionError(f"未知实体 {entity_id}")
        friendly = ent.controller == state.friendly_player_id
        lay = self.layout
        if ent.zone == "HAND" and friendly:
            hand = state.hand(ent.controller)
            return m.hand_card(hand.index(ent), len(hand))
        if ent.zone == "PLAY":
            if ent.card_type in BOARD_CARD_TYPES:
                board = state.board(ent.controller)
                count = len(board) + (1 if friendly and extra_friendly_minion else 0)
                return m.board_slot(board.index(ent), count, friendly)
            spots = {
                "HERO": (lay.my_hero, lay.opp_hero),
                "HERO_POWER": (lay.my_hero_power, lay.opp_hero_power),
                "WEAPON": (lay.my_weapon, lay.opp_weapon),
            }
            if ent.card_type in spots:
                mine, theirs = spots[ent.card_type]
                return m.at(mine if friendly else theirs)
        raise ActionError(f"无法确定实体 {entity_id}（{ent.card_type}，位于 {ent.zone}）在屏幕上的位置")

    # ---- high level actions ------------------------------------------------

    def run_option(
        self,
        state: GameState,
        option: Option,
        sub_index: int | None = None,
        target_id: int | None = None,
    ) -> None:
        m = self._prepare()
        lay = self.layout
        if option.type == "END_TURN":
            self._click(m.at(lay.end_turn))
            return
        if option.entity_id is None:
            raise ActionError(f"不支持的操作类型 {option.type}")

        ent = state.entities[option.entity_id]
        source = self.entity_point(m, state, ent.id)
        sub_count = len(option.sub_options)

        def pick_sub() -> None:
            if sub_index is not None:
                self._wait(ANIMATION_WAIT)
                self._click(m.choice_card(sub_index, sub_count, lay.choice_y))

        def pick_target(after_minion_drop: bool = False) -> None:
            if target_id is not None:
                self._wait(ANIMATION_WAIT)
                self._click(self.entity_point(m, state, target_id, extra_friendly_minion=after_minion_drop))

        if ent.zone == "HAND":
            if ent.card_type == "MINION":
                count = len(state.board(state.friendly_player_id))
                self._drag(source, m.board_slot(count, count + 1, friendly=True))
                pick_sub()
                pick_target(after_minion_drop=True)
            elif sub_index is None and target_id is not None:
                self._drag(source, self.entity_point(m, state, target_id))
            else:
                self._drag(source, m.at(lay.board_center))
                pick_sub()
                pick_target()
            return

        is_attack = ent.card_type in ("MINION", "HERO") and target_id is not None and sub_index is None
        if is_attack:
            self._drag(source, self.entity_point(m, state, target_id))
            return

        self._click(source)
        pick_sub()
        pick_target()

    def run_choice(self, state: GameState, choice: Choice, selected: list[int]) -> None:
        m = self._prepare()
        lay = self.layout
        count = len(choice.entities)
        if choice.choice_type == "MULLIGAN":
            for i, eid in enumerate(choice.entities):
                if eid in selected:
                    self._click(m.choice_card(i, count, lay.mulligan_y))
                    self._wait(0.3)
            self._wait(0.3)
            self._click(m.at(lay.mulligan_confirm))
            return
        for eid in selected:
            self._click(m.choice_card(choice.entities.index(eid), count, lay.choice_y))
            self._wait(0.3)

    def preview_layout(self) -> None:
        """Hover over every fixed layout point so positions can be checked."""
        m = self._prepare()
        lay = self.layout
        for point in (lay.end_turn, lay.my_hero, lay.my_hero_power, lay.opp_hero, lay.opp_hero_power, lay.board_center):
            self._move(m.at(point))
            self._wait(0.6)
        for count in (3, 7):
            for i in range(count):
                self._move(m.board_slot(i, count, friendly=True))
                self._wait(0.25)
            for i in range(count):
                self._move(m.board_slot(i, count, friendly=False))
                self._wait(0.25)
        for i in range(5):
            self._move(m.hand_card(i, 5))
            self._wait(0.3)
