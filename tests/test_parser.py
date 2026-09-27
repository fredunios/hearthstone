from pathlib import Path

from hs_assistant.actions import ScreenMapper
from hs_assistant.config import Layout
from hs_assistant.log_reader import PowerLogTailer
from hs_assistant.power_parser import PowerLogParser
from hs_assistant.screen import WindowRect

SAMPLE = Path(__file__).with_name("sample_power.log")


def parse_sample() -> PowerLogParser:
    parser = PowerLogParser()
    for line in PowerLogTailer(fixed_file=SAMPLE).poll():
        parser.feed(line)
    return parser


def test_players_and_friendly_side():
    s = parse_sample().state
    assert s.friendly_player_id == 1
    assert s.opponent_player_id == 2
    assert s.player_names == {1: "Me#1234", 2: "Opponent#5678"}
    assert s.current_player_id == 1
    assert s.mana(1) == (4, 4)


def test_zones():
    s = parse_sample().state
    assert [e.card_id for e in s.hand(1)] == ["CS2_029", "CS2_182"]
    assert len(s.hand(2)) == 1
    board = s.board(1)
    assert [e.card_id for e in board] == ["EX1_066"]
    assert board[0].health == 1
    assert board[0].name == "Acidic Swamp Ooze"
    assert s.hero(1).card_id == "HERO_08"
    assert s.hero(2).health == 30
    assert s.hero_power(1).id == 65


def test_mulligan_finished_and_opponent_choice_ignored():
    assert parse_sample().state.choice is None


def test_options():
    opts = parse_sample().state.options
    assert opts.id == 3
    by_index = {o.index: o for o in opts.options}
    assert by_index[0].type == "END_TURN"
    assert by_index[1].entity_id == 4 and by_index[1].targets == [6, 66]
    assert by_index[2].valid and by_index[2].targets == []
    wrath = by_index[3]
    assert [sub.entity_id for sub in wrath.sub_options] == [71, 72]
    assert wrath.sub_options[0].targets == [66]
    assert wrath.targets == []
    assert not by_index[4].valid


def test_send_option_clears_options():
    parser = parse_sample()
    parser.feed("D 20:00:01.0000000 GameState.SendOption() - selectedOption=2 selectedSubOption=-1 selectedTarget=0 selectedPosition=0")
    assert parser.state.options is None


def test_tailer_starts_at_last_game(tmp_path):
    log = tmp_path / "Power.log"
    old_game = "D 19:00:00.0 GameState.DebugPrintPower() - CREATE_GAME\nD 19:00:00.1 junk\n"
    log.write_text(old_game + SAMPLE.read_text(), encoding="utf-8")
    lines = PowerLogTailer(fixed_file=log).poll()
    assert lines[0].startswith("D 20:00:00.0000001")


def test_screen_mapping_is_centred():
    m = ScreenMapper(Layout(), WindowRect(0, 0, 1920, 1080))
    assert m.board_slot(0, 1, friendly=True)[0] == 960
    left, right = m.board_slot(0, 2, True), m.board_slot(1, 2, True)
    assert left[0] + right[0] == 1920
