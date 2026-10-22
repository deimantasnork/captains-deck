"""The `v` rows view: each column is a group of one-line ticket rows."""

from __future__ import annotations

import contextlib
import importlib.util
import io
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


def _card(bucket: str, task: str):
    card = flow.Card()
    for slot in flow.Card.__slots__:
        setattr(card, slot, "")
    card.badge_color = None
    card.bucket = bucket
    card.id = card.task = task
    card.title = f"title of {task}"
    return card


def _ui():
    home = flow.Home("captain", "/fm", "captain")
    snap = flow.Snapshot()
    snap.homes = [home]
    snap.home = home
    snap.cols = {k: [] for k, _ in flow.COLUMNS}
    first, last = flow.COLUMNS[0][0], flow.COLUMNS[-1][0]
    snap.cols[first] = [_card(first, "a-1"), _card(first, "a-2")]
    snap.cols[last] = [_card(last, "z-1")]
    ui = flow.UI()
    ui.show_landed = True
    ui.collector._publish(snap)
    ui.columns = ui.board_columns(home)
    return ui, snap


def test_v_toggles_the_rows_view():
    ui, _ = _ui()
    flow.parse_input(b"v", ui)
    assert ui.rows_view is True
    flow.parse_input(b"v", ui)
    assert ui.rows_view is False


def test_j_and_k_flow_across_groups_and_skip_empty_ones():
    ui, _ = _ui()
    ui.rows_view = True
    flow.parse_input(b"jj", ui)
    assert (ui.col_idx, ui.card_idx) == (len(ui.columns) - 1, 0)
    flow.parse_input(b"j", ui)  # stays on the last row
    assert (ui.col_idx, ui.card_idx) == (len(ui.columns) - 1, 0)
    flow.parse_input(b"k", ui)
    assert (ui.col_idx, ui.card_idx) == (0, 1)


def test_rows_view_renders_one_line_per_ticket_under_each_title():
    ui, snap = _ui()
    ui.rows_view = True
    with contextlib.redirect_stdout(io.StringIO()):
        ui.render(snap)
    text = [ANSI.sub("", line) for line in ui.last_frame or []]
    titles = [title for _, title in ui.columns]
    assert any(line.startswith(f"{titles[0]} (2)") for line in text)
    assert any(line.startswith(f"{titles[-1]} (1)") for line in text)
    assert sum("title of a-" in line for line in text) == 2
    assert [c.task for *_, c in ui.card_regions] == ["a-1", "a-2", "z-1"]
