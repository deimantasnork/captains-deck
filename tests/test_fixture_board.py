"""The recorded bearings fixture still projects into the deck's columns."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()


def _cards():
    home = flow.Home("captain", str(FIXTURES / "home"), "captain")
    snap = json.loads((FIXTURES / "bearings_snapshot.json").read_text())
    meta_index = flow.build_meta_index([home])
    cols, totals = flow.make_cards(home, snap, meta_index, {}, {}, [home])
    return cols, totals


def test_fixture_snapshot_projects_into_columns():
    cols, totals = _cards()
    assert [c.task for c in cols["charted"]] == ["demo-queued-1", "demo-blocked-2"]
    assert [c.task for c in cols["underway"]] == ["work-1"]
    assert [c.task for c in cols["awaiting_merge"]] == ["done-2"]
    assert [c.task for c in cols["captains_call"]] == ["decision-1"]
    assert [c.task for c in cols["landed"]] == ["landed-1"]
    assert totals["charted"] == 2
    assert totals["landed"] == 1


def test_fixture_badges_meta_and_why_row():
    cols, _ = _cards()
    gates = {c.task: c for c in cols["charted"]}
    assert gates["demo-queued-1"].badge == "\u00b7 queued"
    assert gates["demo-blocked-2"].badge == "\u26d4 blocked"
    assert gates["demo-blocked-2"].blocked_by == "schema-migrate-3"
    work = cols["underway"][0]
    assert work.badge == "\u25cf shipping"
    assert (work.agent, work.model, work.effort) == ("claude", "opus", "xhigh")
    assert work.spawn_epoch > 0
    assert work.worktree == "/tmp/treehouse/demo/1/demo-work-1"
    assert flow.UI.card_why_line(work) == ""  # "harness busy ..." repeats the badge
    assert cols["awaiting_merge"][0].badge == "\u25cd awaits merge"
    assert cols["landed"][0].badge == "\u2713 landed"
    assert cols["landed"][0].artifact == "pull/412-feat-oauth-retry"


def test_fixture_state_files_parse_like_firstmate():
    state = FIXTURES / "home" / "state"
    meta = flow.read_meta(str(state / "work-1.meta"))
    assert meta["harness"] == "claude"
    assert flow.parse_spawn_gen_epoch(meta["spawn_gen"]) > 0
    st_state, st_text, _ = flow.read_status_tail(str(state / "work-1.status"))
    assert st_state == "working"
    assert "auth/client.rs" in st_text


def test_fixture_decision_store_matches_dialog_reader():
    raw = json.loads(
        (FIXTURES / "home" / "state" / "decision-cards" / "decision-1.json").read_text()
    )
    card = raw["card"]
    assert card["type"] == "decision"
    assert [o["value"] for o in card["options"]][:2] == ["staging", "prod"]
    assert card["recommend_value"] == "staging"
