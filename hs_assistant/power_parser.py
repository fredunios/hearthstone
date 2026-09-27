"""Incremental parser for Hearthstone's Power.log.

Only the ``GameState.*`` lines are used. They describe the authoritative game
state as soon as the server sends it, and ``GameState.DebugPrintOptions`` lists
every legal move the client is currently offering the local player.
"""

from __future__ import annotations

import re

from .game_state import (
    PLACEHOLDER_PLAYER_NAME,
    Choice,
    Entity,
    GameState,
    Option,
    OptionsSet,
)

LINE_RE = re.compile(r"^[DWE] [\d:.]+ (?P<src>\w+)\.(?P<method>\w+)\(\) - (?P<body>.*)$")

BRACKET_ID_RE = re.compile(r"\bid=(\d+)")
BRACKET_NAME_RE = re.compile(r"entityName=(.*?) id=\d+")
BRACKET_CARD_RE = re.compile(r"cardId=(\S*)")

GAME_ENTITY_RE = re.compile(r"^GameEntity EntityID=(\d+)")
PLAYER_RE = re.compile(r"^Player EntityID=(\d+) PlayerID=(\d+)")
FULL_CREATE_RE = re.compile(r"^FULL_ENTITY - Creating ID=(\d+) CardID=(\S*)")
FULL_UPDATE_RE = re.compile(r"^FULL_ENTITY - Updating (?:Entity=)?(.+) CardID=(\S*)$")
SHOW_RE = re.compile(r"^(?:SHOW_ENTITY|CHANGE_ENTITY) - Updating Entity=(.+) CardID=(\S*)$")
HIDE_RE = re.compile(r"^HIDE_ENTITY - Entity=(.+) tag=(\S+) value=(\S*)")
TAG_CHANGE_RE = re.compile(r"^TAG_CHANGE Entity=(.+?) tag=(\S+) value=(\S*)")
TAG_RE = re.compile(r"^tag=(\S+) value=(\S*)")

GAME_PLAYER_RE = re.compile(r"PlayerID=(\d+), PlayerName=(.+)$")

OPTIONS_ID_RE = re.compile(r"^id=(\d+)")
OPTION_RE = re.compile(r"^option (\d+) type=(\S+) mainEntity=(.*?)(?: error=(\S+).*)?$")
SUB_OPTION_RE = re.compile(r"^subOption (\d+) entity=(.*?)(?: error=(\S+).*)?$")
TARGET_RE = re.compile(r"^target (\d+) entity=(.*?)(?: error=(\S+).*)?$")

CHOICES_RE = re.compile(
    r"^id=(\d+) Player=(.+?) (?:TaskList=\S* )?ChoiceType=(\S+) CountMin=(\d+) CountMax=(\d+)"
)
CHOICE_SOURCE_RE = re.compile(r"^Source=(.+)$")
CHOICE_ENTITY_RE = re.compile(r"^Entities\[\d+\]=(.+)$")
CHOSEN_RE = re.compile(r"^id=(\d+) ")


class PowerLogParser:
    def __init__(self, state: GameState | None = None) -> None:
        self.state = state or GameState()
        self._current: Entity | None = None
        self._last_option: Option | None = None
        self._last_sub: Option | None = None
        self._sub_indent = 0
        self._in_choices = False
        self.version = 0

    def feed(self, line: str) -> None:
        m = LINE_RE.match(line.rstrip("\r\n"))
        if not m or m["src"] != "GameState":
            return
        method, body = m["method"], m["body"]
        self.version += 1
        if method != "DebugPrintEntityChoices":
            self._in_choices = False
        if method == "DebugPrintPower":
            self._power(body.strip())
        elif method == "DebugPrintGame":
            self._game(body.strip())
        elif method == "DebugPrintOptions":
            self._options(body)
        elif method == "DebugPrintEntityChoices":
            self._choices(body.strip())
        elif method in ("SendOption",):
            self.state.options = None
        elif method in ("SendChoices", "DebugPrintEntitiesChosen"):
            chosen = CHOSEN_RE.match(body.strip())
            choice = self.state.choice
            if choice and (chosen is None or int(chosen[1]) == choice.id):
                self.state.choice = None

    # ---- entity references -------------------------------------------------

    def _resolve(self, ref: str) -> Entity | None:
        ref = ref.strip()
        if not ref:
            return None
        s = self.state
        if ref.startswith("["):
            id_match = BRACKET_ID_RE.search(ref)
            if not id_match:
                return None
            ent = s.entity(int(id_match[1]))
            name = BRACKET_NAME_RE.search(ref)
            if name and name[1] and not ent.name and name[1] != "UNKNOWN ENTITY":
                ent.name = name[1]
            card = BRACKET_CARD_RE.search(ref)
            if card and card[1] and not ent.card_id:
                ent.card_id = card[1]
            return ent
        if ref.isdigit():
            return s.entity(int(ref))
        if ref == "GameEntity":
            return s.game_entity
        pid = self._player_for_name(ref)
        if pid is None:
            return None
        return s.player_entity(pid)

    def _player_for_name(self, name: str) -> int | None:
        s = self.state
        pid = s.player_id_for_name(name)
        if pid is not None:
            return pid
        # The opponent is often logged as a placeholder before their real name
        # appears; bind the new name to the only player we can't name yet.
        unnamed = [
            p for p in s.player_entities
            if s.player_names.get(p, PLACEHOLDER_PLAYER_NAME) == PLACEHOLDER_PLAYER_NAME
        ]
        if len(unnamed) == 1 and name != PLACEHOLDER_PLAYER_NAME:
            s.player_names[unnamed[0]] = name
            return unnamed[0]
        return None

    # ---- DebugPrintPower ---------------------------------------------------

    def _finish_current(self) -> None:
        ent = self._current
        self._current = None
        s = self.state
        if ent is None or s.friendly_player_id is not None:
            return
        # Only the local player's hand cards arrive with a known CardID.
        if ent.zone == "HAND" and ent.card_id and ent.controller:
            s.friendly_player_id = ent.controller

    def _set_tag(self, ent: Entity, tag: str, value: str) -> None:
        ent.tags[tag] = value
        s = self.state
        if tag == "CURRENT_PLAYER" and value == "0":
            pid = next((p for p, e in s.player_entities.items() if e == ent.id), None)
            if pid is not None and pid == s.friendly_player_id:
                s.options = None
        elif tag == "STATE" and value == "COMPLETE" and ent.id == s.game_entity_id:
            s.options = None
            s.choice = None
            s.in_game = False
        elif tag == "MULLIGAN_STATE" and value == "DONE":
            choice = s.choice
            if choice and choice.choice_type == "MULLIGAN":
                pid = next((p for p, e in s.player_entities.items() if e == ent.id), None)
                if pid == choice.player_id:
                    s.choice = None

    def _power(self, body: str) -> None:
        s = self.state
        m = TAG_RE.match(body)
        if m:
            if self._current is not None:
                self._set_tag(self._current, m[1], m[2])
            return
        self._finish_current()

        if body == "CREATE_GAME":
            self.state = s = GameState()
            s.in_game = True
            return
        if m := TAG_CHANGE_RE.match(body):
            ent = self._resolve(m[1])
            if ent is not None:
                self._set_tag(ent, m[2], m[3])
            return
        if m := FULL_CREATE_RE.match(body):
            ent = s.entity(int(m[1]))
            ent.card_id = m[2]
            self._current = ent
            return
        if m := (FULL_UPDATE_RE.match(body) or SHOW_RE.match(body)):
            ent = self._resolve(m[1])
            if ent is not None:
                if m[2] and ent.card_id != m[2]:
                    ent.card_id = m[2]
                    ent.name = ""
                self._current = ent
            return
        if m := HIDE_RE.match(body):
            ent = self._resolve(m[1])
            if ent is not None:
                self._set_tag(ent, m[2], m[3])
            return
        if m := GAME_ENTITY_RE.match(body):
            s.game_entity_id = int(m[1])
            self._current = s.entity(s.game_entity_id)
            return
        if m := PLAYER_RE.match(body):
            eid, pid = int(m[1]), int(m[2])
            s.player_entities[pid] = eid
            self._current = s.entity(eid)
            return

    # ---- DebugPrintGame ----------------------------------------------------

    def _game(self, body: str) -> None:
        if m := GAME_PLAYER_RE.search(body):
            pid, name = int(m[1]), m[2].strip()
            known = self.state.player_names.get(pid)
            if known is None or known == PLACEHOLDER_PLAYER_NAME:
                self.state.player_names[pid] = name

    # ---- DebugPrintOptions -------------------------------------------------

    def _options(self, raw: str) -> None:
        s = self.state
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        if m := OPTIONS_ID_RE.match(body):
            s.options = OptionsSet(int(m[1]))
            self._last_option = self._last_sub = None
            return
        if s.options is None:
            return
        if m := OPTION_RE.match(body):
            ent = self._resolve(m[3])
            opt = Option(int(m[1]), m[2], ent.id if ent else None, (m[4] or "NONE") == "NONE")
            s.options.options.append(opt)
            self._last_option, self._last_sub = opt, None
            # Invalid options can reference the opponent's hero, so only playable
            # ones prove which side is ours.
            if ent is not None and ent.controller and m[2] == "POWER" and opt.valid:
                s.friendly_player_id = ent.controller
            return
        if self._last_option is None:
            return
        if m := SUB_OPTION_RE.match(body):
            ent = self._resolve(m[2])
            sub = Option(int(m[1]), "SUB_OPTION", ent.id if ent else None, (m[3] or "NONE") == "NONE")
            self._last_option.sub_options.append(sub)
            self._last_sub, self._sub_indent = sub, indent
            return
        if m := TARGET_RE.match(body):
            ent = self._resolve(m[2])
            if ent is None or (m[3] or "NONE") != "NONE":
                return
            owner = self._last_sub if self._last_sub and indent > self._sub_indent else self._last_option
            owner.targets.append(ent.id)

    # ---- DebugPrintEntityChoices -------------------------------------------

    def _choices(self, body: str) -> None:
        s = self.state
        if m := CHOICES_RE.match(body):
            pid = self._player_for_name(m[2].strip())
            self._in_choices = pid is None or s.friendly_player_id is None or pid == s.friendly_player_id
            if self._in_choices:
                s.choice = Choice(int(m[1]), pid, m[3], int(m[4]), int(m[5]))
            return
        if not self._in_choices or s.choice is None:
            return
        if m := CHOICE_SOURCE_RE.match(body):
            ent = self._resolve(m[1])
            s.choice.source_id = ent.id if ent else None
        elif m := CHOICE_ENTITY_RE.match(body):
            ent = self._resolve(m[1])
            if ent is not None:
                s.choice.entities.append(ent.id)
