"""Human-readable (Chinese) descriptions of the game state and legal actions.

Shared by the GUI and the LLM agent so both see the same wording.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cards import CardDatabase
from .game_state import Entity, GameState, Option

KEYWORDS = {
    "TAUNT": "嘲讽",
    "DIVINE_SHIELD": "圣盾",
    "STEALTH": "潜行",
    "WINDFURY": "风怒",
    "POISONOUS": "剧毒",
    "LIFESTEAL": "吸血",
    "RUSH": "突袭",
    "CHARGE": "冲锋",
    "FROZEN": "冻结",
    "DORMANT": "休眠",
    "REBORN": "复生",
    "SILENCED": "沉默",
    "IMMUNE": "免疫",
}


@dataclass
class Action:
    """One concrete thing the player can do right now."""

    label: str
    option: Option | None = None
    sub: int | None = None
    target: int | None = None
    choice_ids: list[int] = field(default_factory=list)


class Describer:
    def __init__(self, state: GameState, cards: CardDatabase) -> None:
        self.state = state
        self.cards = cards

    # ---- names ---------------------------------------------------------

    def name(self, entity_id: int | None) -> str:
        if entity_id is None:
            return "?"
        ent = self.state.entities.get(entity_id)
        if ent is None:
            return f"#{entity_id}"
        return self.cards.name(ent.card_id) or ent.name or ent.card_id or "未知卡牌"

    def target_label(self, entity_id: int) -> str:
        s = self.state
        ent = s.entities.get(entity_id)
        if ent is None:
            return self.name(entity_id)
        side = "我方" if ent.controller == s.friendly_player_id else "敌方"
        if ent.card_type == "HERO":
            return f"{side}英雄"
        if ent.card_type in ("MINION", "LOCATION") and ent.zone == "PLAY":
            return f"{side} {self.describe_minion(ent)}"
        return f"{side} {self.name(entity_id)}"

    def card_text(self, ent: Entity) -> str:
        return self.cards.text(ent.card_id)

    def describe_minion(self, ent: Entity, with_text: bool = False) -> str:
        words = [label for tag, label in KEYWORDS.items() if ent.flag(tag)]
        extra = f" [{'、'.join(words)}]" if words else ""
        if ent.card_type == "LOCATION":
            base = f"{self.name(ent.id)}（地标，剩余 {ent.health} 次）{extra}"
        else:
            base = f"{self.name(ent.id)} {ent.int_tag('ATK')}/{ent.health}{extra}"
        if with_text and (text := self.card_text(ent)):
            base += f"  - {text}"
        return base

    def describe_hand_card(self, ent: Entity) -> str:
        base = f"({ent.int_tag('COST')}) {self.name(ent.id)}"
        if ent.card_type in ("MINION", "WEAPON"):
            second = ent.int_tag("DURABILITY") if ent.card_type == "WEAPON" else ent.int_tag("HEALTH")
            base += f" {ent.int_tag('ATK')}/{second}"
        text = self.card_text(ent)
        return f"{base}  - {text}" if text else base

    # ---- board ---------------------------------------------------------

    def board_text(self, detailed: bool = False) -> str:
        s = self.state
        me, opp = s.friendly_player_id, s.opponent_player_id
        if me is None:
            return "等待对局开始..."
        out: list[str] = []

        def hero_lines(pid: int | None, label: str) -> None:
            hero = s.hero(pid)
            avail, total = s.mana(pid)
            name = s.player_names.get(pid, "?") if pid else "?"
            hp = f"生命 {hero.health}" if hero else "生命 ?"
            armor = hero.int_tag("ARMOR") if hero else 0
            if armor:
                hp += f" +{armor} 护甲"
            if hero and hero.int_tag("ATK"):
                hp += f"，攻击力 {hero.int_tag('ATK')}"
            hero_name = self.name(hero.id) if hero else "?"
            out.append(f"{label}：{name} - {hero_name}，{hp}")
            out.append(f"  法力 {avail}/{total} | 手牌 {len(s.hand(pid))} | 牌库 {s.deck_count(pid)}")
            secrets = s.secrets(pid)
            if secrets:
                names = "、".join(self.name(e.id) for e in secrets) if pid == me else f"{len(secrets)} 个（未知）"
                out.append(f"  奥秘：{names}")
            weapon = s.weapon(pid)
            if weapon:
                durability = weapon.int_tag("DURABILITY") - weapon.int_tag("DAMAGE")
                out.append(f"  武器：{self.name(weapon.id)} {weapon.int_tag('ATK')}/{durability}")
            power = s.hero_power(pid)
            if power:
                used = "（本回合已使用）" if power.flag("EXHAUSTED") else ""
                text = f" - {self.card_text(power)}" if detailed and self.card_text(power) else ""
                out.append(f"  英雄技能：{self.name(power.id)} ({power.int_tag('COST')}){used}{text}")

        def board_lines(pid: int | None) -> None:
            board = s.board(pid)
            out.append("  场面：" + ("（空）" if not board else ""))
            out.extend(f"    {i}. {self.describe_minion(e, with_text=detailed)}" for i, e in enumerate(board, 1))

        hero_lines(opp, "对手")
        board_lines(opp)
        out.append("")
        out.append("-" * 40 if not detailed else "")
        board_lines(me)
        hero_lines(me, "我方")
        out.append("  手牌：")
        out.extend(f"    {i}. {self.describe_hand_card(e)}" for i, e in enumerate(s.hand(me), 1))
        return "\n".join(out)

    # ---- actions -------------------------------------------------------

    def verb(self, opt: Option) -> str:
        ent = self.state.entities.get(opt.entity_id or -1)
        if ent is None:
            return "使用"
        if ent.zone == "HAND":
            return "打出"
        if ent.card_type == "HERO_POWER":
            return "英雄技能"
        if ent.card_type == "LOCATION":
            return "使用地标"
        if ent.card_type in ("MINION", "HERO") and opt.targets:
            return "攻击："
        return "使用"

    def option_title(self, opt: Option) -> str:
        verb = self.verb(opt)
        if verb == "英雄技能":
            return f"英雄技能 {self.name(opt.entity_id)}"
        return f"{verb} {self.name(opt.entity_id)}"

    def option_actions(self) -> list[Action]:
        """Every legal (option, sub option, target) combination."""
        s = self.state
        if not s.options:
            return []
        actions: list[Action] = []
        for opt in s.options.options:
            if opt.type == "END_TURN":
                actions.append(Action("结束回合", opt))
        for opt in s.options.options:
            if opt.type != "POWER" or not opt.valid:
                continue
            title = self.option_title(opt)
            if opt.sub_options:
                for sub in opt.sub_options:
                    if not sub.valid:
                        continue
                    sub_title = f"{title} / 抉择：{self.name(sub.entity_id)}"
                    if sub.targets:
                        actions.extend(Action(f"{sub_title} -> {self.target_label(t)}", opt, sub.index, t) for t in sub.targets)
                    else:
                        actions.append(Action(sub_title, opt, sub.index))
            elif opt.targets:
                actions.extend(Action(f"{title} -> {self.target_label(t)}", opt, None, t) for t in opt.targets)
            else:
                actions.append(Action(title, opt))
        return actions

    def choice_actions(self) -> list[Action]:
        """Discover-style picks (one card each). Mulligan is handled separately."""
        choice = self.state.choice
        if not choice or choice.choice_type == "MULLIGAN":
            return []
        actions = []
        for eid in choice.entities:
            label = f"选择 {self.name(eid)}"
            ent = self.state.entities.get(eid)
            if ent and (text := self.card_text(ent)):
                label += f" - {text}"
            actions.append(Action(label, choice_ids=[eid]))
        return actions
