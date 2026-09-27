"""LLM agent that picks one legal action at a time and executes it."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .actions import ActionError, Executor
from .cards import CardDatabase
from .describe import Action, Describer
from .game_state import GameState
from .mouse import MouseAborted

DEFAULT_MODEL = os.environ.get("HS_AGENT_MODEL", "gpt-5.4")
STABLE_SECONDS = 1.2  # no new log lines for this long = animations have settled
ACTION_TIMEOUT = 12.0  # how long to wait for the game to react to an action
MAX_FAILED_ATTEMPTS = 2

SYSTEM_PROMPT = """你是一名顶尖的炉石传说玩家，正在替用户操作这局游戏。

每次调用时你会收到：当前完整的战场信息、本回合已执行的操作，以及一份带编号的“可选操作”清单。
清单来自游戏客户端，里面的每一项此刻都是合法的。你必须且只能从中选择一项，
系统会立刻执行它，之后你会拿到更新后的战场再选下一步。

决策要点：
- 先检查能否斩杀对手（计算场攻、直伤、武器、英雄技能）。
- 注意操作顺序：例如先打出增益/战吼再攻击，先解掉嘲讽再打脸，先用随从交换再用法术。
- 高效利用法力值，避免浪费；但不要为了用光法力而打出明显亏损的牌。
- 评估交换：优先用低价值随从换掉对方高威胁随从，保护己方关键随从。
- 从手牌打出的随从会被放在己方场面最右侧。
- 只有在没有任何有价值的操作时才选择“结束回合”。

reasoning 用简体中文写，简明扼要（不超过 3 句），说明为什么选这一步。"""

MULLIGAN_PROMPT = """你是一名顶尖的炉石传说玩家，正在进行起手调度。
根据我方职业、对手职业和起手牌，决定要替换哪些牌（通常保留低费、能在前几回合打出的关键牌）。
reasoning 用简体中文写，不超过 3 句。"""

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "action_index": {"type": "integer"},
    },
    "required": ["reasoning", "action_index"],
    "additionalProperties": False,
}

MULLIGAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "replace_indices": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["reasoning", "replace_indices"],
    "additionalProperties": False,
}


@dataclass
class Snapshot:
    state: GameState
    version: int
    idle_seconds: float


class Agent:
    def __init__(
        self,
        snapshot: Callable[[], Snapshot],
        executor: Executor,
        cards: CardDatabase,
        log: Callable[[str], None],
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
    ) -> None:
        self.snapshot = snapshot
        self.executor = executor
        self.cards = cards
        self.log = log
        self.model = model
        self.effort = effort
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client = None
        self._history: list[str] = []
        self._history_turn = -1
        self.single_step = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, single_step: bool = False) -> None:
        if self.running:
            return
        if not os.environ.get("OPENAI_API_KEY"):
            self.log("Agent：没有找到 OPENAI_API_KEY，请先激活虚拟环境再启动助手")
            return
        self.single_step = single_step
        self._stop.clear()
        self.executor.abort.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="agent")
        self._thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        self.executor.abort.set()
        self.log("Agent：收到停止指令")

    # ---- main loop -----------------------------------------------------

    def _run(self) -> None:
        mode = "（单步）" if self.single_step else ""
        self.log(f"Agent 已启动{mode}，模型 {self.model}，思考强度 {self.effort}。按 F9 可随时停止")
        waiting_logged = False
        failures = 0
        last_key = None
        try:
            while not self._stop.is_set():
                snap = self._wait_stable()
                if snap is None:
                    break
                s = snap.state
                if s.is_game_over:
                    self.log("Agent：对局结束，自动停止")
                    break
                key = self._decision_key(s)
                if key is None:
                    if not waiting_logged:
                        self.log("Agent：等待轮到我方...")
                        waiting_logged = True
                    self._stop.wait(0.5)
                    continue
                waiting_logged = False
                if key == last_key:
                    failures += 1
                    if failures >= MAX_FAILED_ATTEMPTS:
                        self.log("Agent：操作多次没有生效，自动停止。请检查点击位置")
                        break
                else:
                    failures = 0
                last_key = key

                acted = self._decide_and_act(s)
                if not acted or self.single_step or self._stop.is_set():
                    break
                self._wait_for_change(key)
        except (MouseAborted, ActionError) as exc:
            self.log(f"Agent：{exc}")
        except Exception as exc:  # noqa: BLE001 - surface API/network errors in the GUI
            self.log(f"Agent 出错：{type(exc).__name__}: {exc}")
        finally:
            self.log("Agent 已停止")

    def _decision_key(self, s: GameState) -> tuple | None:
        """Identifies the decision the game is waiting for, or None if it's not ours."""
        if s.choice and s.choice.entities:
            return ("choice", s.choice.id)
        if s.options and s.current_player_id == s.friendly_player_id:
            return ("options", s.options.id)
        return None

    def _wait_stable(self) -> Snapshot | None:
        while not self._stop.is_set():
            snap = self.snapshot()
            if snap.idle_seconds >= STABLE_SECONDS:
                return snap
            self._stop.wait(0.2)
        return None

    def _wait_for_change(self, key: tuple) -> None:
        deadline = time.monotonic() + ACTION_TIMEOUT
        while not self._stop.is_set() and time.monotonic() < deadline:
            if self._decision_key(self.snapshot().state) != key:
                return
            self._stop.wait(0.3)

    # ---- decisions -----------------------------------------------------

    def _decide_and_act(self, s: GameState) -> bool:
        d = Describer(s, self.cards)
        if s.choice and s.choice.choice_type == "MULLIGAN":
            return self._mulligan(s, d)
        actions = d.choice_actions() if s.choice else d.option_actions()
        if not actions:
            return False

        if self._history_turn != s.turn:
            self._history, self._history_turn = [], s.turn
        history = "\n".join(f"- {h}" for h in self._history) or "（无）"
        numbered = "\n".join(f"{i}. {a.label}" for i, a in enumerate(actions))
        prompt = (
            f"第 {s.turn} 回合，轮到我方。\n\n"
            f"【战场】\n{d.board_text(detailed=True)}\n\n"
            f"【本回合已执行的操作】\n{history}\n\n"
            f"【可选操作】\n{numbered}"
        )
        self.log(f"Agent：思考中...（{len(actions)} 个可选操作）")
        result = self._ask(SYSTEM_PROMPT, prompt, ACTION_SCHEMA, "hearthstone_action")
        if self._stop.is_set():
            return False
        index = result.get("action_index")
        if not isinstance(index, int) or not 0 <= index < len(actions):
            self.log(f"Agent：模型返回了无效的编号 {index}")
            return False
        action = actions[index]
        self.log(f"Agent 选择：{action.label}\n    理由：{result.get('reasoning', '')}")
        self._execute(s, action)
        self._history.append(action.label)
        return True

    def _mulligan(self, s: GameState, d: Describer) -> bool:
        choice = s.choice
        assert choice is not None
        cards = "\n".join(
            f"{i}. {d.describe_hand_card(s.entities[eid])}" for i, eid in enumerate(choice.entities) if eid in s.entities
        )
        me, opp = s.hero(s.friendly_player_id), s.hero(s.opponent_player_id)
        prompt = (
            f"我方英雄：{d.name(me.id) if me else '?'}；对手英雄：{d.name(opp.id) if opp else '?'}\n"
            f"{'我方先手' if len(choice.entities) <= 3 else '我方后手（会额外获得幸运币）'}\n\n"
            f"【起手牌】\n{cards}\n\n请给出要替换的牌的编号列表（可以为空）。"
        )
        self.log("Agent：思考调度中...")
        result = self._ask(MULLIGAN_PROMPT, prompt, MULLIGAN_SCHEMA, "hearthstone_mulligan")
        if self._stop.is_set():
            return False
        indices = [i for i in result.get("replace_indices", []) if isinstance(i, int) and 0 <= i < len(choice.entities)]
        replace = [choice.entities[i] for i in indices]
        names = "、".join(d.name(e) for e in replace) or "不替换"
        self.log(f"Agent 调度：替换 {names}\n    理由：{result.get('reasoning', '')}")
        self.executor.run_choice(s, choice, replace)
        return True

    def _execute(self, s: GameState, action: Action) -> None:
        if action.choice_ids:
            assert s.choice is not None
            self.executor.run_choice(s, s.choice, action.choice_ids)
        else:
            assert action.option is not None
            self.executor.run_option(s, action.option, action.sub, action.target)

    def _ask(self, system: str, prompt: str, schema: dict, name: str) -> dict:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI()
        started = time.monotonic()
        response = self._client.responses.create(
            model=self.model,
            reasoning={"effort": self.effort},
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        )
        self.log(f"Agent：模型用时 {time.monotonic() - started:.1f} 秒")
        return json.loads(response.output_text)
