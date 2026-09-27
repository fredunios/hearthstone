from __future__ import annotations

import copy
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from .actions import ActionError, Executor
from .agent import DEFAULT_MODEL, Agent, Snapshot
from .cards import CardDatabase
from .describe import Action, Describer
from .game_state import GameState
from .hotkey import start_hotkey_listener
from .log_reader import PowerLogTailer
from .log_setup import enable_power_log, is_power_log_enabled
from .mouse import MouseAborted
from .power_parser import PowerLogParser

POLL_MS = 250
UI_FONT = "Microsoft YaHei UI"


class App:
    def __init__(self, root: tk.Tk, tailer: PowerLogTailer, cards: CardDatabase, executor: Executor) -> None:
        self.root = root
        self.tailer = tailer
        self.cards = cards
        self.executor = executor
        self.parser = PowerLogParser()
        self._lock = threading.Lock()
        self._last_line_time = time.monotonic()
        self.busy = False
        self._action_key: tuple | None = None
        self._pending_log: list[str] = []
        self._cards_ready_seen = False
        self._mulligan_vars: dict[int, tk.BooleanVar] = {}
        self.agent = Agent(self.snapshot, executor, cards, self._pending_log.append, model=DEFAULT_MODEL)

        root.title("炉石传说助手")
        root.geometry("1200x800")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=UI_FONT, size=10)

        top = ttk.Frame(root, padding=6)
        top.pack(fill="x")
        self.status = ttk.Label(top, text="启动中...")
        self.status.pack(side="left")
        self.dry_run = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="演练模式（只移动鼠标，不点击）", variable=self.dry_run).pack(side="right")
        ttk.Button(top, text="预览点击位置", command=self._preview_layout).pack(side="right", padx=6)
        if not is_power_log_enabled():
            ttk.Button(top, text="启用 Power.log", command=self._enable_log).pack(side="right", padx=6)

        agent_bar = ttk.Frame(root, padding=(6, 0, 6, 6))
        agent_bar.pack(fill="x")
        ttk.Label(agent_bar, text=f"Agent（{DEFAULT_MODEL}）：").pack(side="left")
        self.start_btn = ttk.Button(agent_bar, text="启动 Agent", command=lambda: self._start_agent(False))
        self.start_btn.pack(side="left")
        self.step_btn = ttk.Button(agent_bar, text="单步", command=lambda: self._start_agent(True))
        self.step_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(agent_bar, text="停止 Agent（F9）", command=self._stop_all)
        self.stop_btn.pack(side="left", padx=4)
        ttk.Label(agent_bar, text="思考强度：").pack(side="left", padx=(12, 0))
        self.effort = tk.StringVar(value="medium")
        ttk.Combobox(agent_bar, textvariable=self.effort, values=["low", "medium", "high"], width=8, state="readonly").pack(side="left")
        self.agent_status = ttk.Label(agent_bar, text="")
        self.agent_status.pack(side="left", padx=12)

        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6)

        self.board_text = tk.Text(body, font=(UI_FONT, 11), wrap="word", state="disabled", width=60)
        body.add(self.board_text, weight=3)

        right = ttk.Frame(body)
        body.add(right, weight=2)
        ttk.Label(right, text="可执行的操作", font=(UI_FONT, 11, "bold")).pack(anchor="w")
        canvas = tk.Canvas(right, highlightthickness=0)
        scroll = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        self.actions = ttk.Frame(canvas)
        self.actions.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.actions, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-e.delta // 120, "units"))

        self.log = tk.Text(root, height=10, font=(UI_FONT, 9), state="disabled")
        self.log.pack(fill="x", padx=6, pady=6)

        hotkey = start_hotkey_listener(self._on_hotkey)
        hotkey.wait(1.0)
        if getattr(hotkey, "ok", False):
            self._write_log("随时按 F9（在游戏里也可以）即可停止 Agent 和正在执行的操作。")
        else:
            self._write_log("注意：F9 快捷键注册失败（可能被其他程序占用），请用“停止 Agent”按钮。")
        self._write_log("手动操作执行中，自己移动鼠标也会中止。")
        root.after(POLL_MS, self._poll)

    # ---- state access --------------------------------------------------

    @property
    def state(self) -> GameState:
        return self.parser.state

    def snapshot(self) -> Snapshot:
        """Thread-safe copy of the current game state for the agent and executor."""
        with self._lock:
            return Snapshot(
                copy.deepcopy(self.parser.state),
                self.parser.version,
                time.monotonic() - self._last_line_time,
            )

    def describer(self) -> Describer:
        return Describer(self.state, self.cards)

    def _write_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ") + msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---- polling -------------------------------------------------------

    def _poll(self) -> None:
        try:
            lines = self.tailer.poll()
        except OSError as exc:
            lines = []
            self.status.configure(text=f"读取日志出错：{exc}")
        if lines:
            with self._lock:
                for line in lines:
                    self.parser.feed(line)
                self._last_line_time = time.monotonic()
        while self._pending_log:
            self._write_log(self._pending_log.pop(0))
        cards_changed = self.cards.ready and not self._cards_ready_seen
        self._cards_ready_seen = self.cards.ready
        if lines or cards_changed:
            self._render_board()
        self._render_actions()
        self._render_status()
        self.root.after(POLL_MS, self._poll)

    def _render_status(self) -> None:
        s = self.state
        if self.tailer.path is None:
            text = "等待 Power.log（请启用日志并重启炉石传说）"
        elif not s.in_game and not s.entities:
            text = f"正在监视 {self.tailer.path.parent.name} - 尚未开始对局"
        elif s.is_game_over:
            text = "对局结束"
        else:
            whose = "你的回合" if s.current_player_id == s.friendly_player_id else "对手回合"
            text = f"第 {s.turn} 回合 - {whose}"
        if self.cards.error:
            text += " | 无法加载卡牌名称"
        elif not self.cards.ready:
            text += " | 正在加载卡牌名称..."
        self.status.configure(text=text)

        running = self.agent.running
        self.agent_status.configure(text="● 运行中" if running else "○ 未运行", foreground="green" if running else "gray")
        idle = "disabled" if running else "normal"
        self.start_btn.configure(state=idle)
        self.step_btn.configure(state=idle)

    def _render_board(self) -> None:
        self.board_text.configure(state="normal")
        self.board_text.delete("1.0", "end")
        self.board_text.insert("end", self.describer().board_text())
        self.board_text.configure(state="disabled")

    # ---- actions panel -------------------------------------------------

    def _render_actions(self) -> None:
        s = self.state
        key = (
            id(s),
            s.options.id if s.options else None,
            len(s.options.options) if s.options else 0,
            s.choice.id if s.choice else None,
            len(s.choice.entities) if s.choice else 0,
            self.busy,
            self.agent.running,
            self.cards.ready,
        )
        if key == self._action_key:
            return
        self._action_key = key
        for child in self.actions.winfo_children():
            child.destroy()

        if self.agent.running:
            ttk.Label(self.actions, text="Agent 正在操作，按 F9 或“停止 Agent”可接管。").pack(anchor="w")
            return
        if self.busy:
            ttk.Label(self.actions, text="执行中...（请不要移动鼠标）").pack(anchor="w")
            return
        d = self.describer()
        if s.choice and s.choice.entities:
            self._render_choice(d)
            return
        if not s.options:
            ttk.Label(self.actions, text="当前没有可执行的操作（不是你的回合或正在等待）。").pack(anchor="w")
            return

        frames: dict[int, ttk.LabelFrame] = {}
        for action in d.option_actions():
            opt = action.option
            assert opt is not None
            if opt.type == "END_TURN":
                ttk.Button(self.actions, text=action.label, command=lambda a=action: self._run(a)).pack(fill="x", pady=(0, 8))
                continue
            frame = frames.get(opt.index)
            if frame is None:
                frame = ttk.LabelFrame(self.actions, text=d.option_title(opt), padding=4)
                frame.pack(fill="x", pady=3)
                frames[opt.index] = frame
            text = action.label.removeprefix(d.option_title(opt)).strip() or action.label
            ttk.Button(frame, text=text, command=lambda a=action: self._run(a)).pack(fill="x")

    def _render_choice(self, d: Describer) -> None:
        choice = self.state.choice
        assert choice is not None
        if choice.choice_type == "MULLIGAN":
            ttk.Label(self.actions, text="调度：勾选要【替换】的卡牌").pack(anchor="w")
            self._mulligan_vars = {}
            for eid in choice.entities:
                var = tk.BooleanVar(value=False)
                self._mulligan_vars[eid] = var
                ttk.Checkbutton(self.actions, text=d.name(eid), variable=var).pack(anchor="w")
            ttk.Button(self.actions, text="确认调度", command=self._confirm_mulligan).pack(fill="x", pady=6)
            return
        source = f"（{d.name(choice.source_id)}）" if choice.source_id else ""
        ttk.Label(self.actions, text=f"选择一张卡牌{source}：").pack(anchor="w")
        for action in d.choice_actions():
            ttk.Button(self.actions, text=action.label, command=lambda a=action: self._run(a)).pack(fill="x")

    # ---- manual execution ----------------------------------------------

    def _start(self, description: str, work) -> None:
        if self.busy or self.agent.running:
            return
        self.busy = True
        self.executor.dry_run = self.dry_run.get()
        self.executor.abort.clear()
        prefix = "[演练] " if self.executor.dry_run else ""
        self._write_log(f"{prefix}{description}（{self.executor.start_delay:g} 秒后开始，请松开鼠标）")

        def worker() -> None:
            try:
                work()
            except (ActionError, MouseAborted) as exc:
                self._pending_log.append(f"失败：{exc}")
            except Exception as exc:  # noqa: BLE001 - report anything, keep the GUI alive
                self._pending_log.append(f"错误：{type(exc).__name__}: {exc}")
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _run(self, action: Action) -> None:
        snap = self.snapshot().state
        if action.choice_ids:
            choice = snap.choice
            if choice is None:
                return
            self._start(action.label, lambda: self.executor.run_choice(snap, choice, action.choice_ids))
            return
        # Re-find the option inside the snapshot so the executor never touches live state.
        assert action.option is not None
        opt = next((o for o in (snap.options.options if snap.options else []) if o.index == action.option.index), None)
        if opt is None:
            self._write_log("操作已过期，请等待新的选项")
            return
        self._start(action.label, lambda: self.executor.run_option(snap, opt, action.sub, action.target))

    def _confirm_mulligan(self) -> None:
        snap = self.snapshot().state
        choice = snap.choice
        if choice is None:
            return
        replace = [eid for eid, var in self._mulligan_vars.items() if var.get()]
        d = self.describer()
        names = "、".join(d.name(e) for e in replace) or "不替换"
        self._start(f"调度：替换 {names}", lambda: self.executor.run_choice(snap, choice, replace))

    def _preview_layout(self) -> None:
        self._start("预览点击位置", self.executor.preview_layout)

    def _enable_log(self) -> None:
        if enable_power_log():
            self._write_log("已更新 log.config。请重启炉石传说，使其开始写入 Power.log。")
        else:
            self._write_log("Power.log 已经启用。")

    # ---- agent ---------------------------------------------------------

    def _start_agent(self, single_step: bool) -> None:
        if self.busy:
            return
        self.executor.dry_run = self.dry_run.get()
        self.agent.effort = self.effort.get()
        if self.executor.dry_run and not single_step:
            self._write_log("演练模式下 Agent 不会真正操作，自动改为单步模式")
            single_step = True
        self.agent.start(single_step=single_step)

    def _stop_all(self) -> None:
        self.executor.abort.set()
        self.agent.stop()

    def _on_hotkey(self) -> None:
        # Runs on the hotkey thread; only touch thread-safe primitives here.
        self.executor.abort.set()
        self.agent.stop()
        self._pending_log.append("F9：已停止")
