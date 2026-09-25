"""Fleet All crew tab: merge planned/running columns across homes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()
Home = flow.Home
Card = flow.Card
merge_fleet_columns = flow.merge_fleet_columns
fleet_planned_running_count = flow.fleet_planned_running_count
inject_all_crew = flow.inject_all_crew
ALL_CREW_LABEL = flow.ALL_CREW_LABEL


def _card(task: str, home_path: str, bucket: str = "underway") -> Card:
    c = Card()
    c.task = task
    c.id = task
    c.home_path = home_path
    c.bucket = bucket
    return c


def test_inject_all_crew_prepends_tab():
    homes = [Home("captain", "/a", "captain"), Home("mate", "/b", "secondmate")]
    out = inject_all_crew(homes)
    assert out[0].label == ALL_CREW_LABEL
    assert out[0].kind == "all"


def test_merge_skips_landed_and_sorts_by_crew():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-x", "/mate", "secondmate")
    cols_c = {"charted": [_card("a", "/captain", "charted")], "landed": [_card("old", "/captain", "landed")]}
    cols_m = {"underway": [_card("b", "/mate")]}
    totals_c = {"charted": 1, "landed": 1}
    totals_m = {"underway": 1}
    cols, totals = merge_fleet_columns(
        [(captain, cols_c, totals_c), (mate, cols_m, totals_m)]
    )
    assert len(cols["landed"]) == 0
    assert [c.task for c in cols["charted"]] == ["a"]
    assert [c.task for c in cols["underway"]] == ["b"]
    assert totals["landed"] == 1


def test_fleet_planned_running_count_excludes_landed():
    homes = inject_all_crew([Home("captain", "/a", "captain")])
    counts = {"captain": 5}
    landed = {"captain": 2}
    assert fleet_planned_running_count(homes, counts, landed) == 3


def test_merge_dedupes_captains_call_by_task():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-x", "/mate", "secondmate")
    dup_a = _card("fpr-issue-29", "/mate", "captains_call")
    dup_a.owner = "demo-lane"
    dup_b = _card("fpr-issue-29", "/mate", "captains_call")
    dup_b.owner = "(main)"
    cols_c = {"captains_call": [dup_a]}
    cols_m = {"captains_call": [dup_b]}
    totals_c = {"captains_call": 1}
    totals_m = {"captains_call": 1}
    cols, totals = merge_fleet_columns(
        [(captain, cols_c, totals_c), (mate, cols_m, totals_m)]
    )
    assert [c.task for c in cols["captains_call"]] == ["fpr-issue-29"]
    assert cols["captains_call"][0].owner == "demo-lane"
    assert totals["captains_call"] == 1
