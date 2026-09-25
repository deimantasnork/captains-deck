"""Fleet All crew tab: merge planned/running columns across homes."""

from __future__ import annotations

import importlib.util
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
Home = flow.Home
Card = flow.Card
merge_fleet_columns = flow.merge_fleet_columns
fleet_planned_running_count = flow.fleet_planned_running_count
inject_all_crew = flow.inject_all_crew
ALL_CREW_LABEL = flow.ALL_CREW_LABEL


def _card(
    task: str,
    home_path: str,
    bucket: str = "underway",
    *,
    task_id: str = "",
    doing: str = "",
    worktree: str = "",
    live: str = "",
    pane: str = "",
) -> Card:
    c = Card()
    c.task = task
    c.id = task_id or task
    c.home_path = home_path
    c.bucket = bucket
    c.doing = doing
    c.worktree = worktree
    c.live_status = live
    c.pane_id = pane
    c.run_elapsed = ""
    c.run_tokens = ""
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


def test_merge_folds_parent_mirror_into_owner_row():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-racket-finder", "/mate2", "secondmate", mate_id="racket-finder")
    mirror = _card(
        "fpr-issue-217",
        "/captain",
        task_id="racket-finder/fpr-issue-217",
        pane="w3A:p2",
        live="working",
    )
    owner = _card("fpr-issue-217", "/mate2", pane="w3A:p2", live="working")
    cols, totals = merge_fleet_columns(
        [
            (captain, {"underway": [mirror]}, {"underway": 1}),
            (mate, {"underway": [owner]}, {"underway": 1}),
        ]
    )
    assert [c.id for c in cols["underway"]] == ["fpr-issue-217"]
    assert cols["underway"][0].home_path == "/mate2"
    assert totals["underway"] == 1


def test_merge_folds_mirror_into_owner_bucket():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-racket-finder", "/mate2", "secondmate", mate_id="racket-finder")
    mirror = _card(
        "fpr-sentry-sourcemaps",
        "/captain",
        "underway",
        task_id="racket-finder/fpr-sentry-sourcemaps",
    )
    owner = _card("fpr-sentry-sourcemaps", "/mate2", "awaiting_merge")
    cols, totals = merge_fleet_columns(
        [
            (captain, {"underway": [mirror]}, {"underway": 1}),
            (mate, {"awaiting_merge": [owner]}, {"awaiting_merge": 1}),
        ]
    )
    assert cols["underway"] == []
    assert [c.id for c in cols["awaiting_merge"]] == ["fpr-sentry-sourcemaps"]
    assert totals["underway"] == 0
    assert totals["awaiting_merge"] == 1


def test_merge_keeps_same_named_tasks_without_evidence():
    one = Home("2ndmate-letspadel-app", "/mate1", "secondmate", mate_id="letspadel-app")
    two = Home("2ndmate-racket-finder", "/mate2", "secondmate", mate_id="racket-finder")
    cols, totals = merge_fleet_columns(
        [
            (
                one,
                {"underway": [_card("issue-42", "/mate1", worktree="/wt/one")]},
                {"underway": 1},
            ),
            (
                two,
                {"underway": [_card("issue-42", "/mate2", worktree="/wt/two")]},
                {"underway": 1},
            ),
        ]
    )
    assert [c.home_path for c in cols["underway"]] == ["/mate1", "/mate2"]
    assert totals["underway"] == 2


def test_merge_folds_rows_sharing_a_run_id():
    one = Home("captain", "/captain", "captain")
    two = Home("2ndmate-x", "/mate", "secondmate", mate_id="x")
    doing = "validating (running) \u00b7 run: 01M3D485GTH0DCJ47D6DEMFCD4"
    cols, totals = merge_fleet_columns(
        [
            (one, {"underway": [_card("issue-7", "/captain", doing=doing)]}, {"underway": 1}),
            (two, {"underway": [_card("issue-7", "/mate", doing=doing)]}, {"underway": 1}),
        ]
    )
    assert len(cols["underway"]) == 1
    assert totals["underway"] == 1


def test_merge_folds_one_home_listing_task_twice():
    home = Home("captain", "/captain", "captain")
    gate = _card("issue-9", "/captain", "charted")
    live = _card("issue-9", "/captain", "underway")
    cols, totals = merge_fleet_columns(
        [
            (
                home,
                {"charted": [gate], "underway": [live]},
                {"charted": 1, "underway": 1},
            )
        ]
    )
    assert cols["charted"] == []
    assert [c.bucket for c in cols["underway"]] == ["underway"]
    assert totals["charted"] == 0
    assert totals["underway"] == 1


def test_fleet_planned_running_count_subtracts_folded_duplicates():
    homes = inject_all_crew(
        [Home("captain", "/a", "captain"), Home("mate", "/b", "secondmate")]
    )
    counts = {"captain": 2, "mate": 2}
    assert fleet_planned_running_count(homes, counts, {}, duplicates=1) == 3
