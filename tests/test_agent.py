from hs_assistant.agent import Agent, Snapshot
from hs_assistant.cards import CardDatabase
from hs_assistant.describe import Describer

from .test_parser import parse_sample


class FakeExecutor:
    def __init__(self):
        import threading

        self.abort = threading.Event()
        self.calls = []

    def run_option(self, state, option, sub=None, target=None):
        self.calls.append((option.index, sub, target))

    def run_choice(self, state, choice, selected):
        self.calls.append(("choice", selected))


def test_actions_are_expanded_per_target():
    d = Describer(parse_sample().state, CardDatabase())
    labels = [a.label for a in d.option_actions()]
    assert labels[0] == "结束回合"
    assert sum("-> 敌方英雄" in label for label in labels) == 2  # Fireball + Wrath sub option
    assert not any("Fireblast" in label for label in labels)  # not enough mana


def test_agent_executes_the_chosen_action():
    state = parse_sample().state
    executor = FakeExecutor()
    logs = []
    agent = Agent(lambda: Snapshot(state, 0, 10.0), executor, CardDatabase(), logs.append)
    actions = Describer(state, CardDatabase()).option_actions()
    wanted = next(i for i, a in enumerate(actions) if a.target == 66 and a.sub is None)
    prompts = []

    def fake_ask(system, prompt, schema, name):
        prompts.append(prompt)
        return {"reasoning": "打脸", "action_index": wanted}

    agent._ask = fake_ask
    assert agent._decide_and_act(state)
    assert executor.calls == [(1, None, 66)]
    assert "【可选操作】" in prompts[0] and f"{wanted}. " in prompts[0]
    assert any("打脸" in line for line in logs)
