#!/usr/bin/env python3
"""Home discovery and --once: explicit-only homes, and the fleet All merge.

Run: python3 tests/test_flow_homes.py   (or via pytest)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SNAPSHOT = ROOT / "tests" / "fixtures" / "bearings_snapshot.json"

_spec = importlib.util.spec_from_file_location(
    "flow_tui", ROOT / "scripts" / "flow_tui.py"
)
assert _spec is not None and _spec.loader is not None
flow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flow)


class _MonkeyPatch:
    """Small stand-in for pytest's monkeypatch so this file runs standalone."""

    def __init__(self) -> None:
        self._undo: list[tuple] = []

    def setenv(self, name: str, value: str) -> None:
        self._undo.append(("env", name, os.environ.get(name)))
        os.environ[name] = value

    def delenv(self, name: str) -> None:
        self._undo.append(("env", name, os.environ.get(name)))
        os.environ.pop(name, None)

    def setattr(self, obj, name: str, value) -> None:
        self._undo.append(("attr", (obj, name), getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self) -> None:
        while self._undo:
            kind, key, old = self._undo.pop()
            if kind == "env":
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
            else:
                obj, name = key
                setattr(obj, name, old)


@contextlib.contextmanager
def _patches():
    mp = _MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


def _make_home(root: Path, name: str, captain: bool = False) -> Path:
    path = root / name
    (path / "bin").mkdir(parents=True)
    (path / "data" / "task-one").mkdir(parents=True)
    if captain:
        (path / "data" / "captain.md").write_text("captain\n")
    stub = path / "bin" / "fm-bearings-snapshot.sh"
    stub.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\ncat \"{FIXTURE_SNAPSHOT}\"\n")
    stub.chmod(0o755)
    return path


def _isolate(mp: _MonkeyPatch, tmp_path: Path) -> Path:
    """A temp HOME with a home at the default scan path, plus no fleet noise."""
    mp.setenv("HOME", str(tmp_path))
    mp.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    mp.delenv("FM_HOME")
    mp.delenv("HERDR_CONFIG_DIR")
    mp.setattr(flow, "_live_agent_homes", lambda: set())
    return _make_home(tmp_path, "firstmate", captain=True)


def _paths(homes) -> set[str]:
    return {os.path.realpath(h.path) for h in homes if h.path}


def test_default_discovery_scans_the_usual_home(tmp_path: Path) -> None:
    with _patches() as mp:
        scan_home = _isolate(mp, tmp_path)
        explicit = _make_home(tmp_path, "explicit-home")
        mp.setenv("FM_FLOW_HOMES", f"explicit={explicit}")
        paths = _paths(flow.discover_homes())
    assert os.path.realpath(explicit) in paths
    assert os.path.realpath(scan_home) in paths


def test_homes_only_limits_discovery_to_the_explicit_list(tmp_path: Path) -> None:
    with _patches() as mp:
        scan_home = _isolate(mp, tmp_path)
        explicit = _make_home(tmp_path, "explicit-home")
        mp.setenv("FM_FLOW_HOMES", f"explicit={explicit}")
        mp.setenv("FM_FLOW_HOMES_ONLY", "1")
        paths = _paths(flow.discover_homes())
    assert os.path.realpath(explicit) in paths
    assert os.path.realpath(scan_home) not in paths


def test_homes_only_without_a_list_falls_back_to_the_scan(tmp_path: Path) -> None:
    with _patches() as mp:
        scan_home = _isolate(mp, tmp_path)
        mp.setenv("FM_FLOW_HOMES_ONLY", "1")
        paths = _paths(flow.discover_homes())
    assert os.path.realpath(scan_home) in paths


def test_once_all_merges_the_fleet_instead_of_an_empty_all(tmp_path: Path) -> None:
    with _patches() as mp:
        _isolate(mp, tmp_path)
        home = _make_home(tmp_path, "crew-one")
        mp.setenv("FM_FLOW_HOMES", f"crew-one={home}")
        mp.setenv("FM_FLOW_HOMES_ONLY", "1")
        mp.setattr(flow, "herdr_agents", lambda: ({}, {}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = flow.once(all_homes=True)
        text = out.getvalue()
    assert rc == 0
    assert "== All  (1 homes)" in text, text
    assert "== crew-one" in text, text
    assert "== All  ()" not in text, text
    # the merged All and the home itself each carry the fixture's two gates
    assert text.count("Charted Next (2)") == 2, text


def test_once_home_all_is_the_merged_fleet(tmp_path: Path) -> None:
    with _patches() as mp:
        _isolate(mp, tmp_path)
        home = _make_home(tmp_path, "crew-one")
        mp.setenv("FM_FLOW_HOMES", f"crew-one={home}")
        mp.setenv("FM_FLOW_HOMES_ONLY", "1")
        mp.setattr(flow, "herdr_agents", lambda: ({}, {}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = flow.once(active_label=flow.ALL_CREW_LABEL)
        text = out.getvalue()
    assert rc == 0
    assert "== All  (1 homes)" in text, text
    assert "== All  ()" not in text, text


def main() -> int:
    failures = 0
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(Path(tmp))
            except Exception as exc:  # noqa: BLE001 - standalone runner report
                failures += 1
                print(f"NOT OK - {name}: {exc!r}")
            else:
                print(f"ok - {name}")
    if failures:
        print(f"\n{failures} home check(s) failed")
        return 1
    print("\nall home checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
