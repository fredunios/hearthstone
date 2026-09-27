from __future__ import annotations

from dataclasses import dataclass, field

PLACEHOLDER_PLAYER_NAME = "UNKNOWN HUMAN PLAYER"

BOARD_CARD_TYPES = ("MINION", "LOCATION")


@dataclass
class Entity:
    id: int
    card_id: str = ""
    name: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    def tag(self, name: str, default: str = "") -> str:
        return self.tags.get(name, default)

    def int_tag(self, name: str, default: int = 0) -> int:
        value = self.tags.get(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def flag(self, name: str) -> bool:
        return self.int_tag(name) != 0

    @property
    def zone(self) -> str:
        return self.tag("ZONE")

    @property
    def zone_pos(self) -> int:
        return self.int_tag("ZONE_POSITION")

    @property
    def controller(self) -> int:
        return self.int_tag("CONTROLLER")

    @property
    def card_type(self) -> str:
        return self.tag("CARDTYPE")

    @property
    def health(self) -> int:
        return self.int_tag("HEALTH") - self.int_tag("DAMAGE")


@dataclass
class Option:
    index: int
    type: str
    entity_id: int | None
    valid: bool
    targets: list[int] = field(default_factory=list)
    sub_options: list[Option] = field(default_factory=list)


@dataclass
class OptionsSet:
    id: int
    options: list[Option] = field(default_factory=list)


@dataclass
class Choice:
    id: int
    player_id: int | None
    choice_type: str
    count_min: int
    count_max: int
    source_id: int | None = None
    entities: list[int] = field(default_factory=list)


class GameState:
    def __init__(self) -> None:
        self.entities: dict[int, Entity] = {}
        self.game_entity_id: int | None = None
        # PlayerID (the value used by the CONTROLLER tag) -> player entity id
        self.player_entities: dict[int, int] = {}
        self.player_names: dict[int, str] = {}
        self.friendly_player_id: int | None = None
        self.options: OptionsSet | None = None
        self.choice: Choice | None = None
        self.in_game = False

    # ---- lookups -------------------------------------------------------

    def entity(self, entity_id: int) -> Entity:
        ent = self.entities.get(entity_id)
        if ent is None:
            ent = Entity(entity_id)
            self.entities[entity_id] = ent
        return ent

    @property
    def game_entity(self) -> Entity | None:
        if self.game_entity_id is None:
            return None
        return self.entities.get(self.game_entity_id)

    @property
    def opponent_player_id(self) -> int | None:
        if self.friendly_player_id is None:
            return None
        for pid in self.player_entities:
            if pid != self.friendly_player_id:
                return pid
        return None

    def player_entity(self, player_id: int | None) -> Entity | None:
        if player_id is None or player_id not in self.player_entities:
            return None
        return self.entities.get(self.player_entities[player_id])

    def player_id_for_name(self, name: str) -> int | None:
        for pid, pname in self.player_names.items():
            if pname == name:
                return pid
        return None

    @property
    def turn(self) -> int:
        game = self.game_entity
        return game.int_tag("TURN") if game else 0

    @property
    def current_player_id(self) -> int | None:
        for pid, eid in self.player_entities.items():
            ent = self.entities.get(eid)
            if ent and ent.flag("CURRENT_PLAYER"):
                return pid
        return None

    @property
    def is_game_over(self) -> bool:
        game = self.game_entity
        return game is not None and game.tag("STATE") == "COMPLETE"

    def _zone(self, player_id: int | None, zone: str) -> list[Entity]:
        return [
            e for e in self.entities.values()
            if e.controller == player_id and e.zone == zone
        ]

    def hand(self, player_id: int | None) -> list[Entity]:
        return sorted(self._zone(player_id, "HAND"), key=lambda e: e.zone_pos)

    def board(self, player_id: int | None) -> list[Entity]:
        return sorted(
            (e for e in self._zone(player_id, "PLAY") if e.card_type in BOARD_CARD_TYPES),
            key=lambda e: e.zone_pos,
        )

    def deck_count(self, player_id: int | None) -> int:
        return len(self._zone(player_id, "DECK"))

    def secrets(self, player_id: int | None) -> list[Entity]:
        return self._zone(player_id, "SECRET")

    def _in_play_of_type(self, player_id: int | None, card_type: str) -> Entity | None:
        for e in self._zone(player_id, "PLAY"):
            if e.card_type == card_type:
                return e
        return None

    def hero(self, player_id: int | None) -> Entity | None:
        player = self.player_entity(player_id)
        if player is not None:
            hero_id = player.int_tag("HERO_ENTITY")
            if hero_id in self.entities:
                return self.entities[hero_id]
        return self._in_play_of_type(player_id, "HERO")

    def hero_power(self, player_id: int | None) -> Entity | None:
        return self._in_play_of_type(player_id, "HERO_POWER")

    def weapon(self, player_id: int | None) -> Entity | None:
        return self._in_play_of_type(player_id, "WEAPON")

    def mana(self, player_id: int | None) -> tuple[int, int]:
        """Return (available, total) mana crystals."""
        player = self.player_entity(player_id)
        if player is None:
            return 0, 0
        total = player.int_tag("RESOURCES")
        available = (
            total
            - player.int_tag("RESOURCES_USED")
            - player.int_tag("OVERLOAD_LOCKED")
            + player.int_tag("TEMP_RESOURCES")
        )
        return max(available, 0), total
