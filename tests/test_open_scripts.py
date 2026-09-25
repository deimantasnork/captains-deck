#!/usr/bin/env python3
"""Launcher safety checks: only panes this plugin owns are focused or closed.

Runs a fake `herdr` over a JSON state file and drives the real launcher
scripts. Also runs standalone: python3 tests/test_open_scripts.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPEN_FLOW = ROOT / "scripts" / "open-flow.sh"
OPEN_DECK = ROOT / "scripts" / "open-captain-deck.sh"

FAKE_HERDR = r'''#!/usr/bin/env python3
"""Minimal herdr stand-in for launcher tests; state lives in FAKE_HERDR_STATE."""
import json
import os
import sys

STATE = os.environ["FAKE_HERDR_STATE"]
LOG = os.environ["FAKE_HERDR_LOG"]


def load():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {"panes": [], "workspaces": [], "seq": 0}


def save(st):
    with open(STATE, "w") as fh:
        json.dump(st, fh)


def pane_json(p):
    return {
        "pane_id": p["pane_id"],
        "tab_id": p.get("tab_id") or "",
        "workspace_id": p.get("workspace_id") or "",
        "label": p.get("label") or "",
        "title": p.get("label") or "",
        "focused": bool(p.get("focused")),
    }


def main(argv):
    with open(LOG, "a") as fh:
        fh.write(" ".join(argv[1:]) + "\n")
    st = load()
    args = argv[1:]
    if args[:2] == ["pane", "list"]:
        ws = args[args.index("--workspace") + 1] if "--workspace" in args else None
        panes = [
            pane_json(p)
            for p in st["panes"]
            if ws is None or p.get("workspace_id") == ws
        ]
        print(json.dumps({"result": {"panes": panes}}))
        return 0
    if args[:2] == ["pane", "process-info"]:
        pid = args[args.index("--pane") + 1] if "--pane" in args else ""
        pane = next((p for p in st["panes"] if p["pane_id"] == pid), None)
        if pane is None:
            print(json.dumps({"result": {"process_info": {}}}))
            return 0
        procs = [{"argv": pane.get("argv") or []}]
        print(json.dumps({"result": {"process_info": {"foreground_processes": procs}}}))
        return 0
    if args[:2] == ["pane", "close"]:
        pid = args[2]
        st["panes"] = [p for p in st["panes"] if p["pane_id"] != pid]
        save(st)
        return 0
    if args[:3] == ["plugin", "pane", "focus"]:
        pid = args[3]
        for p in st["panes"]:
            p["focused"] = p["pane_id"] == pid
        save(st)
        return 0
    if args[:3] == ["plugin", "pane", "open"]:
        ws = args[args.index("--workspace") + 1] if "--workspace" in args else None
        if ws is None and st["workspaces"]:
            ws = st["workspaces"][0]["workspace_id"]
        st["seq"] += 1
        seq = st["seq"]
        pane = {
            "pane_id": "{}:p{}".format(ws or "w1", seq),
            "tab_id": "{}:t{}".format(ws or "w1", seq),
            "workspace_id": ws or "w1",
            "label": "Flow",
            "focused": "--focus" in args,
            "argv": ["bash", "scripts/kanban-view.sh"],
        }
        if pane["focused"]:
            for p in st["panes"]:
                p["focused"] = False
        st["panes"].append(pane)
        save(st)
        return 0
    if args[:2] == ["workspace", "list"]:
        print(json.dumps({"result": {"workspaces": st["workspaces"]}}))
        return 0
    if args[:2] == ["workspace", "create"]:
        st["seq"] += 1
        seq = st["seq"]
        ws = {
            "workspace_id": "w{}".format(seq),
            "label": args[args.index("--label") + 1] if "--label" in args else "",
        }
        st["workspaces"].append(ws)
        save(st)
        print(json.dumps({"result": {"workspace": ws}}))
        return 0
    if args[:2] == ["tab", "focus"]:
        return 0
    print("unsupported: {}".format(" ".join(args)), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''


def _setup(tmp: str):
    fake = Path(tmp) / "fake-herdr"
    fake.write_text(FAKE_HERDR)
    fake.chmod(0o755)
    state = Path(tmp) / "state.json"
    log = Path(tmp) / "log.txt"
    cfg = Path(tmp) / "cfg"
    cfg.mkdir()
    return fake, state, log, cfg


def _write_state(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


def _read_state(path: Path) -> dict:
    return json.loads(path.read_text())


def _run(script: Path, fake: Path, state: Path, log: Path, cfg: Path):
    env = dict(os.environ)
    env["HERDR_BIN_PATH"] = str(fake)
    env["FAKE_HERDR_STATE"] = str(state)
    env["FAKE_HERDR_LOG"] = str(log)
    env["HERDR_PLUGIN_CONFIG_DIR"] = str(cfg)
    env.pop("HERDR_CONFIG_DIR", None)
    return subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=20
    )


def _pane(pane_id: str, label: str = "Flow", argv=None, focused: bool = False, ws: str = "w1"):
    return {
        "pane_id": pane_id,
        "tab_id": pane_id.split(":")[0] + ":t" + pane_id.split("p")[-1],
        "workspace_id": ws,
        "label": label,
        "focused": focused,
        "argv": argv if argv is not None else ["bash", "-i"],
    }


def test_open_flow_never_closes_a_foreign_flow_pane():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [_pane("w1:p1", focused=True)],
                "workspaces": [{"workspace_id": "w1", "label": "base"}],
                "seq": 1,
            },
        )
        proc = _run(OPEN_FLOW, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        ids = [p["pane_id"] for p in data["panes"]]
        assert "w1:p1" in ids, "the user's Flow-labeled pane survived"
        assert len(ids) == 2, "a fresh overlay opened instead"
        assert "pane close" not in log.read_text()
        opened = next(p["pane_id"] for p in data["panes"] if p["pane_id"] != "w1:p1")
        recorded = (cfg / "flow-overlay.panes").read_text().split()
        assert recorded == [opened], "the opened overlay was recorded"


def test_open_flow_toggle_closes_the_recorded_overlay():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [
                    _pane("w1:p1", argv=["bash", "scripts/kanban-view.sh"], focused=True)
                ],
                "workspaces": [{"workspace_id": "w1", "label": "base"}],
                "seq": 1,
            },
        )
        (cfg / "flow-overlay.panes").write_text("w1:p1\n")
        proc = _run(OPEN_FLOW, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        assert data["panes"] == [], "the recorded overlay was closed"
        assert (cfg / "flow-overlay.panes").read_text().split() == []
        assert "pane close w1:p1" in log.read_text()


def test_open_flow_focuses_a_recorded_overlay_without_reopening():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [
                    _pane("w1:p1", argv=["bash", "scripts/kanban-view.sh"], focused=False)
                ],
                "workspaces": [{"workspace_id": "w1", "label": "base"}],
                "seq": 1,
            },
        )
        (cfg / "flow-overlay.panes").write_text("w1:p1\n")
        proc = _run(OPEN_FLOW, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        assert len(data["panes"]) == 1, "no second overlay was opened"
        assert data["panes"][0]["focused"] is True
        assert "plugin pane focus w1:p1" in log.read_text()


def test_open_flow_adopts_an_unrecorded_live_board():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [
                    _pane("w1:p1", argv=["bash", "scripts/flow_tui.py"], focused=False)
                ],
                "workspaces": [{"workspace_id": "w1", "label": "base"}],
                "seq": 1,
            },
        )
        proc = _run(OPEN_FLOW, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        assert len(data["panes"]) == 1 and data["panes"][0]["focused"] is True
        assert (cfg / "flow-overlay.panes").read_text().split() == ["w1:p1"]


def test_open_flow_ignores_a_malformed_recorded_id():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [_pane("w1:p1", focused=True)],
                "workspaces": [{"workspace_id": "w1", "label": "base"}],
                "seq": 1,
            },
        )
        (cfg / "flow-overlay.panes").write_text("w1:p1;rm -rf /\n")
        proc = _run(OPEN_FLOW, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        assert "w1:p1" in [p["pane_id"] for p in data["panes"]]
        assert "pane close" not in log.read_text()


def test_open_deck_never_closes_an_unrecorded_stale_flow_pane():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [_pane("w1:p1", focused=True)],
                "workspaces": [{"workspace_id": "w1", "label": "captain's deck"}],
                "seq": 1,
            },
        )
        proc = _run(OPEN_DECK, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        ids = [p["pane_id"] for p in data["panes"]]
        assert "w1:p1" in ids, "the unrecorded Flow-labeled pane survived"
        assert len(ids) == 2, "a fresh deck board opened"
        assert "pane close" not in log.read_text()


def test_open_deck_retires_only_its_own_stale_board():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [_pane("w1:p1", focused=True)],
                "workspaces": [{"workspace_id": "w1", "label": "captain's deck"}],
                "seq": 1,
            },
        )
        (cfg / "deck-flow.panes").write_text("w1:p1\n")
        proc = _run(OPEN_DECK, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        ids = [p["pane_id"] for p in data["panes"]]
        assert "w1:p1" not in ids, "the recorded stale board was retired"
        assert len(ids) == 1, "the replacement board is in its place"
        assert (cfg / "deck-flow.panes").read_text().split() == [ids[0]]
        assert "pane close w1:p1" in log.read_text()


def test_open_deck_focuses_a_live_board_instead_of_duplicating():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(
            state,
            {
                "panes": [
                    _pane(
                        "w1:p1",
                        argv=["bash", "scripts/kanban-view.sh"],
                        focused=False,
                    )
                ],
                "workspaces": [{"workspace_id": "w1", "label": "captain's deck"}],
                "seq": 1,
            },
        )
        proc = _run(OPEN_DECK, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        assert len(data["panes"]) == 1 and data["panes"][0]["focused"] is True
        assert (cfg / "deck-flow.panes").read_text().split() == ["w1:p1"]
        assert "plugin pane open" not in log.read_text()


def test_open_deck_creates_workspace_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        fake, state, log, cfg = _setup(tmp)
        _write_state(state, {"panes": [], "workspaces": [], "seq": 0})
        proc = _run(OPEN_DECK, fake, state, log, cfg)
        assert proc.returncode == 0, proc.stderr
        data = _read_state(state)
        assert [w["label"] for w in data["workspaces"]] == ["captain's deck"]
        assert len(data["panes"]) == 1
        assert (cfg / "deck-flow.panes").read_text().split() == [
            data["panes"][0]["pane_id"]
        ]


def main() -> int:
    failures = 0
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - standalone runner report
            failures += 1
            print(f"NOT OK - {name}: {exc!r}")
        else:
            print(f"ok - {name}")
    if failures:
        print(f"\n{failures} launcher check(s) failed")
        return 1
    print("\nall launcher checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
