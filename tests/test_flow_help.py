"""The footer help entry point and its keybinding modal."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def test_question_mark_opens_and_esc_closes_help():
    ui = flow.UI()
    flow.parse_input(b"?", ui)
    assert ui.help is True
    flow.parse_input(b"\x1b", ui)
    assert ui.help is False


def test_help_owns_keys_while_open():
    ui = flow.UI()
    flow.parse_input(b"?", ui)
    flow.parse_input(b"o", ui)  # would open the agent pane otherwise
    assert ui.help is True
    flow.parse_input(b"q")  # closes the help instead of quitting
    assert ui.help is False
    assert ui.quitting is False


def test_footer_hint_click_opens_help():
    ui = flow.UI()
    ui.height = 40
    ui.help_region = (1, 7)
    ui.click(2, 39, 0)
    assert ui.help is True


def test_click_outside_closes_help():
    ui = flow.UI()
    flow.parse_input(b"?", ui)
    ui.help_box = (10, 5, 60, 18)
    ui.click(2, 2, 0)
    assert ui.help is False


def test_render_help_lists_the_footer_commands():
    ui = flow.UI()
    lines = [" " * 90] * 24
    ui.open_help()
    ui.render_help(lines, 90, 24)
    text = ANSI.sub("", "\n".join(lines))
    assert "Help" in text
    assert "move between columns" in text
    assert "open the selected agent pane" in text
    assert "toggle the Landed column" in text
    assert "quit" in text
    assert ui.help_box is not None
