#!/usr/bin/env python3
"""Focused checks for the Captain's Call decision dialog in flow_tui.py.

Covers the pure card lookup / normalization / keyed-line contract and a render
smoke check, without touching a live firstmate home or any intake.

Run: python3 tests/test_flow_decision_dialog.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "flow_tui", os.path.join(ROOT, "scripts", "flow_tui.py")
)
assert _spec is not None and _spec.loader is not None
flow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flow)

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"ok - {msg}")
    else:
        FAILURES.append(msg)
        print(f"NOT OK - {msg}")


BOARD_CARD = {
    "key": "demo-issue-29",
    "type": "decision",
    "repo": "demo-repo",
    "title": "Draft the release checklist for #29",
    "about": "Needs a maintainer to run the flow by hand",
    "decide": "Keep it parked, or drop that half of #29",
    "options": [
        {
            "value": "hold",
            "label": "Hold it",
            "hint": "Leave it parked until someone can pick it up",
        },
        {
            "value": "drop",
            "label": "Drop the extra work",
            "hint": "#29 then stands on its existing evidence alone",
        },
    ],
    "recommend_value": "hold",
    "allow_freeform": True,
    "freeform_hint": "Or describe a different way to proceed",
}


def make_home(root: str, name: str, label: str | None = None) -> Any:
    path = os.path.join(root, name)
    for sub in ("bin", ".lavish", "data"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    open(os.path.join(path, "bin", "fm-bearings-snapshot.sh"), "w").close()
    return flow.Home(label or name, path, "secondmate")


def write_board(home: Any, cards: list[dict]) -> None:
    data = {"schema": "fm-bearings-board.v1", "captains_call": cards}
    html = (
        "<html><body><script id=\"bearings-data\" type=\"application/json\">"
        + json.dumps(data)
        + "</script></body></html>"
    )
    with open(os.path.join(home.path, ".lavish", "bearings-board.html"), "w") as fh:
        fh.write(html)


def write_payload(home: Any, cards: list[dict], name: str = "2026-01-01") -> None:
    data = {"schema": "fm-bearings-board.v1", "captains_call": cards}
    with open(os.path.join(home.path, "data", f"bearings-board-payload-{name}.json"), "w") as fh:
        json.dump(data, fh)


def make_card(task: str = "demo-issue-29", owner: str = "(main)", title: str = "summary", doing: str = "captain-hold") -> Any:
    card = flow.Card()
    card.id = task
    card.task = task
    card.bucket = "captains_call"
    card.title = title
    card.badge = "\u2691 captain"
    card.badge_color = flow.C_DECIDE
    card.doing = doing
    card.agent = card.model = card.effort = ""
    card.kind = card.mode = card.worktree = card.branch = ""
    card.pane_id = card.workspace_id = card.tab_id = ""
    card.artifact = card.blocked_by = ""
    card.live_status = card.status_text = ""
    card.owner = owner
    card.home_path = ""
    return card


def main() -> int:
    with tempfile.TemporaryDirectory() as root:
        main_home = make_home(root, "captain", label="captain")
        mate_home = make_home(root, "mate", label="2ndmate-demo-lane")
        write_board(mate_home, [BOARD_CARD])
        write_payload(mate_home, [{**BOARD_CARD, "title": "payload copy"}])
        write_payload(mate_home, [{"key": "payload-only-task", "type": "decision",
                                   "title": "Payload only", "options": [
                                       {"value": "go", "label": "Go"}],
                                   "allow_freeform": True}])

        # board file wins over the payload history
        found = flow.decision_card_for(BOARD_CARD["key"], mate_home)
        check(found.get("title") == "Draft the release checklist for #29",
              "live board card wins over the payload file")

        # payload fallback when the board does not carry the key
        found = flow.decision_card_for("payload-only-task", mate_home)
        check(found.get("title") == "Payload only", "payload file is the fallback source")

        # owner home wins over the board on screen
        write_board(main_home, [{**BOARD_CARD, "title": "active board copy"}])
        card = make_card(owner="demo-lane")
        content = flow.decision_card_content(card, [main_home, mate_home], main_home)
        check(content["title"] == "Draft the release checklist for #29",
              "the owning home's card wins over the active board")
        check(content["home_path"] == mate_home.path,
              "the dialog routes answers to the owning home")

        # fleet board on captain home while the mate crew tab is active
        mate_only_board = make_home(root, "mate-tab", label="2ndmate-demo-lane")
        write_board(main_home, [BOARD_CARD])
        mate_tab_card = make_card(owner="demo-lane")
        mate_tab_content = flow.decision_card_content(
            mate_tab_card, [main_home, mate_only_board], mate_only_board
        )
        check(
            mate_tab_content["title"] == "Draft the release checklist for #29",
            "captain fleet board is found when a mate crew tab is active",
        )
        check(
            [o["value"] for o in mate_tab_content["options"]]
            == ["hold", "drop", "reconcile"],
            "authored options survive when only the mate tab is on screen",
        )

        # main-home rows route to the active home
        main_card = make_card(owner="(main)")
        main_content = flow.decision_card_content(main_card, [main_home, mate_home], main_home)
        check(main_content["home_path"] == main_home.path, "(main) rows route to the active home")

        # fleet All tab: (main) must not erase the snapshot home stamped on the card
        all_home = flow.Home(flow.ALL_CREW_LABEL, "", "all")
        fleet_card = make_card(owner="(main)")
        fleet_card.home_path = mate_home.path
        fleet_content = flow.decision_card_content(
            fleet_card, flow.inject_all_crew([main_home, mate_home]), all_home
        )
        check(
            fleet_content["home_path"] == mate_home.path,
            "All tab falls back to the card's snapshot home for (main) rows",
        )

        # an unresolved mate owner refuses rather than guessing a home
        lost = make_card(owner="vanished-mate")
        lost_content = flow.decision_card_content(lost, [main_home, mate_home], main_home)
        check(lost_content["home_path"] == "", "an unresolvable owner carries no answering home")

        # the durable store outlives a board rebuild: below the live board, above
        # the payload history
        store_dir = os.path.join(mate_home.path, "state", "decision-cards")
        os.makedirs(store_dir, exist_ok=True)
        with open(os.path.join(store_dir, "payload-only-task.json"), "w") as fh:
            json.dump(
                {
                    "schema": "fm-decision-card.v1",
                    "generated": "2026-01-01T00:00:00Z",
                    "card": {**BOARD_CARD, "key": "payload-only-task", "title": "Durable store copy"},
                },
                fh,
            )
        with open(os.path.join(store_dir, "demo-issue-29.json"), "w") as fh:
            json.dump(
                {
                    "schema": "fm-decision-card.v1",
                    "generated": "2026-01-01T00:00:00Z",
                    "card": {**BOARD_CARD, "title": "Store copy loses to the board"},
                },
                fh,
            )
        check(
            flow.decision_card_for("payload-only-task", mate_home).get("title") == "Durable store copy",
            "the durable store beats the payload history",
        )
        check(
            flow.decision_card_for("demo-issue-29", mate_home).get("title")
            == "Draft the release checklist for #29",
            "the live board still beats the durable store",
        )

        # a real hold reason becomes the about line when no card is composed
        reason_card = make_card(
            task="no-composed-card-3",
            title="Short issue title",
            doing="The hold reason sentence.",
        )
        reason_content = flow.decision_card_content(reason_card, [main_home, mate_home], main_home)
        check(reason_content["title"] == "Short issue title",
              "a card-less ticket keeps its durable short title")
        check(reason_content["about"] == "The hold reason sentence.",
              "a card-less ticket turns its hold reason into the about line")
        check(reason_content["decide"] != "",
              "a card-less ticket still says what the captain has to do")

        # normalization: reconcile injected once, recommendation kept, freeform
        values = [o["value"] for o in content["options"]]
        check(values == ["hold", "drop", "reconcile"],
              "the standard reconcile choice is injected last")
        check(content["recommend"] == "hold", "the recommendation is carried")
        check(content["freeform"] is True and content["close"] == "",
              "freeform and close mode are carried")

        # a board that already carries reconcile is not duplicated
        with_reconcile = {
            **BOARD_CARD,
            "options": BOARD_CARD["options"] + [dict(flow.RECONCILE_OPTION)],
        }
        write_board(mate_home, [with_reconcile])
        dup = flow.decision_card_for(BOARD_CARD["key"], mate_home)
        check([o["value"] for o in dup["options"]].count("reconcile") == 1,
              "an already-injected reconcile is kept single")

        # keyed lines: recommended preselected, note appended, close mode last
        dialog = flow.DecisionDialog(content)
        check(dialog.selected == 0, "the recommended option starts selected")
        check(dialog.display_answer() == "hold", "selection is the answer")
        check(dialog.keyed_line() == "demo-issue-29\thold\t"
              "Draft the release checklist for #29 -> hold",
              "the keyed answer line is key, answer, label")
        dialog.note = "ask the reviewer first"
        check(dialog.keyed_line() == "demo-issue-29\thold - ask the reviewer first\t"
              "Draft the release checklist for #29 -> hold - ask the reviewer first",
              "a note rides the answer after the selection")

        # reconcile keeps the note as request provenance and is never an answer
        dialog.selected = 2
        dialog.note = "already moot?"
        check(dialog.selected_option()["value"] == "reconcile", "reconcile is selectable")
        check(dialog.reconcile_line() == "demo-issue-29\talready moot?",
              "reconcile carries the note as provenance")

        # no composed card: the summary is the title and only reconcile is listed
        bare = make_card(task="no-composed-card", title="Run #29's release checklist")
        bare_content = flow.decision_card_content(bare, [main_home, mate_home], main_home)
        check(bare_content["title"] == "Run #29's release checklist",
              "a card-less ticket keeps its summary as the title")
        check([o["value"] for o in bare_content["options"]] == ["reconcile"],
              "a card-less ticket still offers reconcile and freeform answer")

        # the snapshot summary "<title>: <reason>" splits into title + about
        split = make_card(
            task="no-composed-card-2",
            title="Run #29's release checklist: Waits on the captain: five s\u2026",
        )
        split_content = flow.decision_card_content(split, [main_home, mate_home], main_home)
        check(split_content["title"] == "Run #29's release checklist",
              "a card-less summary keeps the issue title")
        check(split_content["about"] == "Waits on the captain: five s\u2026",
              "a card-less summary turns its reason into the about line")

        # ---- render smoke: the modal and the cleaned-up header ----
        snap = flow.Snapshot()
        mate_count_zero = make_home(root, "mate-zero", label="2ndmate-scout")
        snap.homes = [main_home, mate_count_zero]
        snap.home = main_home
        snap.cols = {key: [] for key, _ in flow.COLUMNS}
        snap.cols["captains_call"] = [card]
        snap.totals = {"captains_call": 1}
        snap.activity = {}
        snap.counts = {main_home.label: 1, mate_count_zero.label: 0}
        snap.collected_at = time.time()
        snap.loading = False
        snap.error = ""

        ui = flow.UI()
        ui.dialog = flow.DecisionDialog(content)
        ui.dialog.note = "typed note"
        frame = io.StringIO()
        with contextlib.redirect_stdout(frame):
            ui.render(snap)
        text = ANSI.sub("", frame.getvalue())
        for needle in ("DECISION", "demo-repo", "Draft the release checklist for #29",
                       "ABOUT", "DECIDE", "Hold it", "REC", "Reconcile",
                       "Queue answer", "typed note", "esc close"):
            check(needle in text, f"dialog renders {needle!r}")

        long_note = "sadasdasdsds " + "asdasdasdasd" * 12
        ui_long = flow.UI()
        ui_long.dialog = flow.DecisionDialog(content)
        ui_long.dialog.note = long_note
        ui_long.dialog.focus = "note"
        ui_long._dialog_scroll_follow = True
        frame_long = io.StringIO()
        old_size = flow.shutil.get_terminal_size

        def _short_terminal(_fallback=(120, 40)):
            return os.terminal_size((120, 24))

        flow.shutil.get_terminal_size = _short_terminal
        try:
            with contextlib.redirect_stdout(frame_long):
                ui_long.render(snap)
        finally:
            flow.shutil.get_terminal_size = old_size
        long_text = ANSI.sub("", frame_long.getvalue())
        check("sadasdasdsds" in long_text, "a long note shows wrapped text instead of one-line ellipsis")
        check(long_text.count("asdasdasdasd") >= 2,
              "a long note wraps across multiple card rows")
        check(ui_long._dialog_scroll_max > 0, "a tall dialog enables vertical scroll")
        check("pgup/pgdn or wheel scroll" in long_text and "lines " in long_text,
              "a scrollable dialog shows range and scroll keys")
        check(text.count("\u256d") >= 5,
              "each option and the note row render as their own bordered card")
        check("2ndmate-scout (0)" in text, "a visited empty crew shows (0)")
        check("(1)" in text, "a count is shown when known")
        check("updated" not in text and "bearings 20s" not in text and "live 2s" not in text,
              "the header freshness diagnostics are hidden")
        check("\u25b2" not in text and "\u25bc" not in text,
              "column headers carry no scroll arrows")
        check(text.count("DECISION") == 1, "the type badge is shown once, on the card's own header")
        check(ui.dialog_hit, "option/action rows register click regions")

        # ---- input routing ----
        nav = flow.UI()
        nav.dialog = flow.DecisionDialog(content)
        flow.parse_input(b"\x1b[B", nav)
        check(nav.dialog.cursor == 1, "down arrow moves the option cursor")
        flow.parse_input(b" ", nav)
        check(nav.dialog.selected == 1, "space picks the cursor option")
        flow.parse_input(b"x", nav)
        check(nav.dialog.note == "x" and nav.dialog.focus == "note",
              "a printable key edits the note")
        flow.parse_input("\u00e9".encode("utf-8"), nav)
        check(nav.dialog.note == "x\u00e9", "a multibyte character reaches the note whole")
        flow.parse_input(b" ", nav)
        check(nav.dialog.note == "x\u00e9 ", "a space types into a focused note")
        flow.parse_input(b"\x7f", nav)
        check(nav.dialog.note == "x\u00e9", "backspace edits the note")
        flow.parse_input(b"\x1b", nav)
        check(nav.dialog is None, "esc closes the dialog")

        # submit with no owning home refuses visibly instead of crashing
        dead = flow.UI()
        dead.dialog = flow.DecisionDialog(bare_content)
        dead.dialog_submit()
        for _ in range(50):
            if dead.dialog.error:
                break
            time.sleep(0.02)
        check(bool(dead.dialog.error), "a submit with no owning home reports a refusal")

        # the exact intake calls: keyed answer, card-declared release, and the
        # bound reconcile-request path that never touches `answers`
        calls: list[tuple[str, list[str], str]] = []
        original_run = getattr(flow, "_run_captain_hold")
        original_bind = getattr(flow, "ensure_decision_binding")
        try:
            setattr(
                flow,
                "_run_captain_hold",
                lambda home, args, stdin_text: (
                    calls.append((home, list(args), stdin_text)) or (True, "")
                ),
            )
            setattr(
                flow,
                "ensure_decision_binding",
                lambda home: (
                    calls.append((home, ["binding-and-bind"], "")) or (True, "")
                ),
            )
            answer_dialog = flow.DecisionDialog(content)
            answer_dialog.home_path = mate_home.path
            flow.run_submit(answer_dialog)
            check(
                calls[-1][1] == ["answers", "--source", "herdr-firstmate-flow captain's deck"],
                "a normal choice feeds the one keyed-answer intake",
            )
            check(
                calls[-1][2] == "demo-issue-29\thold\t"
                "Draft the release checklist for #29 -> hold\n",
                "the keyed line is stdin, exactly once",
            )
            release_content = dict(content)
            release_content["close"] = "release"
            release_dialog = flow.DecisionDialog(release_content)
            release_dialog.home_path = mate_home.path
            flow.run_submit(release_dialog)
            check(
                calls[-1][2].rstrip("\n").split("\t")[-1] == "release",
                "a captain-gated work card declares its release close mode",
            )
            reconcile_dialog = flow.DecisionDialog(content)
            reconcile_dialog.home_path = mate_home.path
            reconcile_dialog.selected = 2
            reconcile_dialog.note = "premise may be stale"
            flow.run_submit(reconcile_dialog)
            check(
                calls[-2][1] == ["binding-and-bind"],
                "reconcile binds the source before requesting",
            )
            check(
                calls[-1][1][:2] == ["reconcile-requests", "--source-id"]
                and flow.DECISION_SOURCE_ID in calls[-1][1],
                "reconcile goes to the reconcile-request intake",
            )
            check(
                calls[-1][2] == "demo-issue-29\tpremise may be stale\n",
                "reconcile sends the key and the note as provenance",
            )
        finally:
            setattr(flow, "_run_captain_hold", original_run)
            setattr(flow, "ensure_decision_binding", original_bind)

        # a mate-owned call wakes its lane through the parent home's fm-send
        wake_log = os.path.join(root, "wake.log")
        os.makedirs(os.path.join(main_home.path, "state"), exist_ok=True)
        open(os.path.join(main_home.path, "state", "demo-lane.meta"), "w").close()
        send_stub = os.path.join(main_home.path, "bin", "fm-send.sh")
        with open(send_stub, "w") as fh:
            fh.write(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "{wake_log}"\n')
        os.chmod(send_stub, 0o755)
        wake_card = make_card(owner="demo-lane")
        wake_content = flow.decision_card_content(wake_card, [main_home, mate_home], main_home)
        check(
            wake_content["wake_home"] == main_home.path and wake_content["wake_lane"] == "demo-lane",
            "a mate-owned call resolves its parent lane for the wake",
        )
        wake_dialog = flow.DecisionDialog(wake_content)
        wake_dialog.home_path = mate_home.path
        original_run2 = getattr(flow, "_run_captain_hold")
        try:
            setattr(flow, "_run_captain_hold", lambda home, args, stdin_text: (True, ""))
            ok, detail = flow.run_submit(wake_dialog)
        finally:
            setattr(flow, "_run_captain_hold", original_run2)
        check(ok and detail == "", "a delivered wake leaves no warning beside the queued state")
        recorded = open(wake_log).read().splitlines() if os.path.exists(wake_log) else []
        check(
            recorded[:2] == ["demo-lane", "--fire-and-forget"] and len(recorded[2]) == 16,
            "the wake steers the lane with an idempotent delivery id",
        )
        check(
            "captain's deck answer for demo-issue-29" in (recorded[3] if len(recorded) > 3 else ""),
            "the wake carries the captain's answer",
        )

        cols = {key: [] for key, _ in flow.COLUMNS}
        cols["charted"] = [object()]
        cols["underway"] = [object(), object()]
        cols["landed"] = [object()] * 6
        totals = {"charted": 1, "underway": 2, "landed": 6}
        total, landed = flow.board_ticket_count(cols, totals)
        check(total == 9 and landed == 6, "board_ticket_count sums every column")

        snap = flow.Snapshot()
        snap.counts = {"crew-a": 9}
        snap.landed_counts = {"crew-a": 6}
        ui = flow.UI()
        ui.show_landed = False
        check(ui.crew_tab_count(snap, "crew-a") == 3, "hidden Landed is omitted from crew tab totals")
        ui.show_landed = True
        check(ui.crew_tab_count(snap, "crew-a") == 9, "visible Landed is included in crew tab totals")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "plugins", "config", "herdr-firstmate-flow")
            os.makedirs(cfg)
            open(os.path.join(cfg, "show_landed"), "w").write("0\n")
            old_cfg = os.environ.get("HERDR_CONFIG_DIR")
            os.environ["HERDR_CONFIG_DIR"] = tmp
            try:
                ui2 = flow.UI()
                check(not ui2.show_landed, "show_landed file 0 hides Landed on startup")
                flow.parse_input(b"L", ui2)
                check(ui2.show_landed, "Shift+L toggles Landed on")
                with open(os.path.join(cfg, "show_landed")) as fh:
                    check(fh.read().strip() == "1", "L toggle persists show_landed=1")
            finally:
                if old_cfg is None:
                    os.environ.pop("HERDR_CONFIG_DIR", None)
                else:
                    os.environ["HERDR_CONFIG_DIR"] = old_cfg

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
