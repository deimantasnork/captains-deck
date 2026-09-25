#!/usr/bin/env python3
"""Captain's Deck <-> Firstmate contract check.

The deck reads a small set of Firstmate surfaces from the home and only ever
writes through the guarded answer intakes. Firstmate updates itself, so this
checker asserts those surfaces still exist and still parse:

  - ``bin/fm-bearings-snapshot.sh --json --fields paths`` and the sections /
    item fields ``make_cards()`` reads
  - ``state/<task>.meta`` keys the card uses
  - ``state/<task>.status`` tail lines (``<state> [at=<epoch>]: <text>``)
  - ``state/decision-cards/<task>.json`` (the ``fm-decision-card.v1`` store)
  - ``bin/fm-captain-hold.sh`` / ``bin/fm-send.sh`` exist and are executable

Run it after any ``updatefirstmate``, or against a fresh clone in CI:

    scripts/fm-contract-check.sh --home ~/firstmate
    python3 scripts/fm_contract_check.py --home /tmp/firstmate --json

Exit status: 0 contract holds, 1 drift found, 2 no usable home.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import stat
import subprocess
import sys

SNAPSHOT_SCRIPT = os.path.join("bin", "fm-bearings-snapshot.sh")
HOLD_SCRIPT = os.path.join("bin", "fm-captain-hold.sh")
SEND_SCRIPT = os.path.join("bin", "fm-send.sh")
REQUIRED_SCRIPTS = (SNAPSHOT_SCRIPT, HOLD_SCRIPT, SEND_SCRIPT)
SNAPSHOT_FLAGS = ("--json", "--fields", "paths")
EXPECTED_SCHEMA = "fm-bearings.v1"
SECTIONS = ("gates", "in_flight", "decisions_open", "landed", "paths")
ITEM_FIELDS = {
    "gates": ("id", "title", "reason", "blocked_by"),
    "in_flight": ("id", "name", "doing", "state"),
    "landed": ("id", "what", "artifact"),
    "paths": ("id", "worktree"),
}
META_KEYS = ("harness", "model", "effort", "spawn_gen")
STATUS_RE = re.compile(
    r"^(?P<state>[a-zA-Z_-]+)(?:\s+\[at=(?P<at>\d+)\])?\s*:?\s*(?P<text>.*)$"
)
CARD_CLOSE = ("done", "release")


class Report:
    """Collected check results for one home."""

    def __init__(self, home: str) -> None:
        self.home = home
        self.checks: list[dict[str, str]] = []

    def add(self, status: str, check: str, detail: str = "") -> None:
        self.checks.append({"status": status, "check": check, "detail": detail})

    def ok(self, check: str, detail: str = "") -> None:
        self.add("ok", check, detail)

    def warn(self, check: str, detail: str) -> None:
        self.add("warn", check, detail)

    def fail(self, check: str, detail: str) -> None:
        self.add("fail", check, detail)

    @property
    def failures(self) -> list[dict[str, str]]:
        return [c for c in self.checks if c["status"] == "fail"]

    def is_ok(self) -> bool:
        return not self.failures

    def counts(self) -> dict[str, int]:
        return {
            status: sum(1 for c in self.checks if c["status"] == status)
            for status in ("ok", "warn", "fail")
        }

    def to_json(self) -> dict:
        return {
            "home": self.home,
            "firstmate_rev": _git_revision(self.home),
            "ok": self.is_ok(),
            "counts": self.counts(),
            "checks": self.checks,
        }

    def print_text(self) -> None:
        rev = _git_revision(self.home)
        suffix = f" (firstmate {rev})" if rev else ""
        print(f"Captain's Deck contract check - {self.home}{suffix}")
        for check in self.checks:
            detail = f": {check['detail']}" if check["detail"] else ""
            print(f"  [{check['status']:4s}] {check['check']}{detail}")
        counts = self.counts()
        print(
            f"  {counts['ok']} ok, {counts['warn']} warning(s), "
            f"{counts['fail']} failure(s)"
        )
        for failure in self.failures:
            print(
                f"  FAIL {failure['check']}: {failure['detail']}",
                file=sys.stderr,
            )


def _git_revision(path: str) -> str:
    """Short commit for a checkout; "" when it is not a git work tree."""
    if not path:
        return ""
    rev = ""
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            rev = (proc.stdout or "").strip()
    except Exception:
        rev = ""
    return rev


def check_home(home: str) -> Report:
    """Run every contract check against one Firstmate home or clone."""
    report = Report(home)
    _check_scripts(home, report)
    data = _run_snapshot(home, report)
    if data is not None:
        _check_snapshot(data, report)
    _check_state(home, report)
    return report


def _check_scripts(home: str, report: Report) -> None:
    missing = []
    for rel in REQUIRED_SCRIPTS:
        path = os.path.join(home, rel)
        try:
            mode = os.stat(path).st_mode
        except OSError:
            missing.append(rel)
            continue
        if not stat.S_ISREG(mode) or not mode & stat.S_IXUSR:
            missing.append(rel)
    if missing:
        report.fail(
            "bin",
            "missing or not executable: " + ", ".join(missing),
        )
    else:
        report.ok("bin", "bearings snapshot + answer/wake intakes executable")


def _run_snapshot(home: str, report: Report) -> dict | None:
    script = os.path.join(home, SNAPSHOT_SCRIPT)
    if not os.path.isfile(script):
        return None
    try:
        proc = subprocess.run(
            [script, *SNAPSHOT_FLAGS],
            capture_output=True,
            text=True,
            timeout=45,
            env=dict(os.environ, FM_HOME=home),
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        report.fail("snapshot", f"{SNAPSHOT_SCRIPT} could not run: {exc!r}")
        return None
    if proc.returncode != 0:
        tail = [
            line
            for line in ((proc.stderr or "") + (proc.stdout or "")).splitlines()
            if line.strip()
        ]
        report.fail(
            "snapshot",
            f"{SNAPSHOT_SCRIPT} exited {proc.returncode}: "
            f"{tail[-1] if tail else 'no output'}",
        )
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        report.fail("snapshot", f"stdout is not JSON: {exc}")
        return None
    if not isinstance(data, dict):
        report.fail("snapshot", "top level is not a JSON object")
        return None
    report.ok("snapshot", f"{SNAPSHOT_SCRIPT} {SNAPSHOT_FLAGS[1]} runs and returns JSON")
    return data


def _check_snapshot(data: dict, report: Report) -> None:
    schema = data.get("schema")
    if schema == EXPECTED_SCHEMA:
        report.ok("snapshot.schema", EXPECTED_SCHEMA)
    else:
        report.warn(
            "snapshot.schema",
            f"unexpected schema {schema!r}; the deck was built for {EXPECTED_SCHEMA}",
        )
    for section in SECTIONS:
        if section not in data:
            report.fail(
                f"snapshot.{section}",
                "section missing (the deck reads it every tick)",
            )
            continue
        items = data.get(section)
        if not isinstance(items, list):
            report.fail(
                f"snapshot.{section}",
                f"expected a list, got {type(items).__name__}",
            )
            continue
        problems: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                problems.append(f"{section}[{index}] is not an object")
                continue
            for field in ITEM_FIELDS.get(section, ()):
                if not isinstance(item.get(field), str):
                    problems.append(
                        f"{section}[{index}].{field} missing or not a str"
                    )
            if section == "decisions_open":
                key = item.get("key")
                if not isinstance(key, str) or not key:
                    problems.append(f"{section}[{index}].key missing or not a str")
                title = item.get("title")
                summary = item.get("summary")
                if not isinstance(title, str) and not isinstance(summary, str):
                    problems.append(
                        f"{section}[{index}] has neither title nor summary"
                    )
        if problems:
            report.fail(
                f"snapshot.{section}",
                f"{len(problems)} issue(s); first: {problems[0]}",
            )
        else:
            report.ok(f"snapshot.{section}", f"{len(items)} item(s)")
    paths = data.get("paths")
    if isinstance(paths, list):
        for index, item in enumerate(paths):
            if isinstance(item, dict) and not isinstance(item.get("status"), str):
                report.warn(
                    f"snapshot.paths[{index}]",
                    "status is not a str (the card falls back to state/<task>.status)",
                )
                break


def _check_state(home: str, report: Report) -> None:
    state = os.path.join(home, "state")
    if not os.path.isdir(state):
        report.warn("state", "no state/ directory yet (fresh clone or unused home)")
        return
    metas = sorted(glob.glob(os.path.join(state, "*.meta")))
    statuses = sorted(glob.glob(os.path.join(state, "*.status")))
    cards = sorted(glob.glob(os.path.join(state, "decision-cards", "*.json")))
    if not metas and not statuses and not cards:
        report.warn("state", "state/ carries no .meta, .status, or decision cards yet")
        return
    _check_meta(metas, report)
    _check_status(statuses, report)
    _check_cards(cards, report)


def _check_meta(paths: list[str], report: Report) -> None:
    if not paths:
        return
    keys: set[str] = set()
    malformed: list[str] = []
    for path in paths:
        try:
            with open(path, errors="replace") as handle:
                for line in handle:
                    if not line.strip() or line.startswith("#"):
                        continue
                    if "=" not in line:
                        malformed.append(os.path.basename(path))
                        continue
                    key, _ = line.split("=", 1)
                    keys.add(key.strip())
        except OSError:
            malformed.append(os.path.basename(path))
    if "harness" in keys:
        report.ok("state/*.meta", f"{len(paths)} file(s), harness= present")
    else:
        report.fail(
            "state/*.meta",
            f"{len(paths)} file(s) but none carries harness= "
            "(the agent line and run stats depend on it)",
        )
    missing = [key for key in META_KEYS if key not in keys]
    if missing:
        report.warn(
            "state/*.meta",
            f"no meta carries {', '.join(missing)} (that part of the card degrades)",
        )
    if malformed:
        report.warn(
            "state/*.meta",
            f"{len(malformed)} file(s) had unparsable lines, e.g. {malformed[0]}",
        )


def _check_status(paths: list[str], report: Report) -> None:
    if not paths:
        return
    bad: list[str] = []
    for path in paths:
        try:
            with open(path, errors="replace") as handle:
                lines = [line for line in handle.read().splitlines() if line.strip()]
        except OSError:
            bad.append(os.path.basename(path))
            continue
        if lines and not STATUS_RE.match(lines[-1]):
            bad.append(os.path.basename(path))
    if bad and len(bad) == len(paths):
        report.fail(
            "state/*.status",
            f"none of {len(paths)} tail(s) match "
            "'<state> [at=<epoch>]: <text>'",
        )
    elif bad:
        report.warn(
            "state/*.status",
            f"{len(bad)}/{len(paths)} tail(s) do not match the state line; "
            f"first: {bad[0]}",
        )
    else:
        report.ok("state/*.status", f"{len(paths)} tail(s) match the state line")


def _check_cards(paths: list[str], report: Report) -> None:
    if not paths:
        return
    problems: list[str] = []
    for path in paths:
        name = os.path.basename(path)
        try:
            with open(path, errors="replace") as handle:
                text = handle.read()
            data = json.loads(text)
        except (OSError, ValueError) as exc:
            if isinstance(exc, json.JSONDecodeError) and exc.msg == "Extra data":
                problems.append(
                    f"{name}: holds more than one JSON document "
                    "(the deck reads a single fm-decision-card.v1 object)"
                )
            else:
                problems.append(f"{name}: {exc}")
            continue
        card = data.get("card") if isinstance(data, dict) else None
        if not isinstance(card, dict):
            problems.append(f"{name}: no top-level card object (fm-decision-card.v1)")
            continue
        options = card.get("options")
        if options is not None and not isinstance(options, list):
            problems.append(f"{name}: options is not a list")
        elif isinstance(options, list):
            for option in options:
                if (
                    not isinstance(option, dict)
                    or not isinstance(option.get("value"), str)
                    or not option["value"]
                ):
                    problems.append(f"{name}: an option has no string value")
                    break
        close = card.get("close")
        if close is not None and close not in CARD_CLOSE:
            problems.append(f"{name}: close={close!r} is not one of {CARD_CLOSE}")
    if problems:
        report.fail(
            "state/decision-cards",
            f"{len(problems)} issue(s); first: {problems[0]}",
        )
    else:
        report.ok(
            "state/decision-cards",
            f"{len(paths)} card(s) parse as fm-decision-card.v1",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Captain's Deck <-> Firstmate contract check",
    )
    parser.add_argument(
        "--home",
        default=os.environ.get("FM_HOME") or os.path.expanduser("~/firstmate"),
        help="Firstmate home or clone (default: $FM_HOME or ~/firstmate)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable report instead of text",
    )
    args = parser.parse_args(argv)
    home = os.path.realpath(os.path.expanduser(args.home))
    if not os.path.isdir(home):
        print(f"no such home: {home}", file=sys.stderr)
        return 2
    report = check_home(home)
    if args.json:
        print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    else:
        report.print_text()
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
