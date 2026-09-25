#!/usr/bin/env python3
"""Firstmate Flow - multi-home kanban TUI for Herdr.

Shows every Firstmate home (captain + secondmates) as a crew tab, projected
into five columns: Charted Next, Underway, Captain's Call, In Review, Landed.

Per ticket it shows a live status badge, the agent harness, model, thinking
effort, and (on click / Enter) focuses the Herdr pane where that ticket's
agent runs, which selects it in the Herdr agents sidebar.

Captain's Call is the one column that acts: clicking (or pressing Enter on) a
ticket opens its composed board card as a decision dialog, and the captain's
choice is piped to firstmate's one keyed-answer intake
(``bin/fm-captain-hold.sh answers``) - or to its reconcile-request intake for
the reserved ``reconcile`` value. Everything else stays read-only, and no state
beyond the screen is kept.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
import unicodedata

# ----------------------------------------------------------------------------
# constants
# ----------------------------------------------------------------------------

# Firstmate's own four bearings sections: gates (Charted Next), in_flight
# (Underway), decisions_open (Captain's Call), landed (Recently Landed).
# Awaiting Merge is a projection of in_flight rows whose Firstmate state is
# "done" (crew finished, work waiting on merge/review) - not a fifth section.
COLUMNS = [
    ("charted", "Charted Next"),
    ("underway", "Underway"),
    ("captains_call", "Captain's Call"),
    ("awaiting_merge", "Awaiting Merge"),
    ("landed", "Landed"),
]

# The Lavish bearings board composes every open call into a card with ABOUT /
# DECIDE context and authored options. The captain's deck mirrors that card in a
# dialog and turns the captain's choice into the keyed line firstmate's one
# answer intake owns (bin/fm-captain-hold.sh answers). This is a channel, not a
# decision-maker: it never resolves a task itself, and the reserved `reconcile`
# value goes to the separate reconcile-request intake, never to `answers`.
DECISION_SOURCE_ID = "herdr-firstmate-flow"
DECISION_SOURCE = "herdr-firstmate-flow captain's deck"
ANSWER_LIMIT = 512  # bytes, the board's own bound
RECONCILE_OPTION = {
    "value": "reconcile",
    "label": "Reconcile",
    "hint": (
        "Re-check the latest state, then close this with evidence "
        "or keep it open with a note"
    ),
}

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
REV = "\x1b[7m"
CLEAR = "\x1b[2J\x1b[H"


def fg(n: int) -> str:
    """ANSI-16 SGR colour. Herdr drives the pane palette from its [theme]
    setting, so basic colours follow the user's theme instead of hardcoding
    256-colour indexes."""
    return f"\x1b[{n}m"


C_BORDER = 90   # bright black
C_ACCENT = 36   # cyan
C_TITLE = 36    # cyan
C_DIM = 90      # bright black
C_OK = 32       # green
C_WARN = 33     # yellow
C_BAD = 31      # red
C_DECIDE = 35   # magenta
C_REVIEW = 36   # cyan
C_QUEUE = 90    # bright black

HOMES_REDISCOVER_SECS = 60.0

# data/ entries that are not task directories
NON_TASK = re.compile(
    r"^(backlog|charter|done-archive|learnings|projects|captain|crew|README|"
    r"stow-receipt|bearings-board-payload).*",
    re.I,
)


def _debug(msg: str) -> None:
    path = os.environ.get("FM_FLOW_DEBUG")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(f"{time.time():.3f} {msg}\n")
    except OSError:
        pass


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 5.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Live badge/agent refresh. FM_FLOW_REFRESH_SECS is honored as a legacy alias.
TICK_SECS = max(
    0.25,
    min(
        60.0,
        _float(
            os.environ.get("FM_FLOW_TICK_SECS") or os.environ.get("FM_FLOW_REFRESH_SECS"),
            2.0,
        ),
    ),
)
# How often the expensive bearings snapshot (columns/paths) is rebuilt.
BEARINGS_TTL = max(2.0, min(600.0, _float(os.environ.get("FM_FLOW_BEARINGS_SECS"), 20.0)))
# Rows scrolled per wheel notch (cards are 8 rows tall, so 3 feels smooth).
WHEEL_ROWS = max(1, min(8, _int(os.environ.get("FM_FLOW_WHEEL_ROWS"), 3)))
# Landed grows forever in Firstmate's archive; show only the newest few.
LANDED_LIMIT = max(0, _int(os.environ.get("FM_FLOW_LANDED_LIMIT"), 10))


def _on(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "")
    if not val:
        val = str(default)
    return val.strip().lower() in ("1", "true", "yes", "on")


def show_landed_default() -> bool:
    """Landed is a Firstmate section, but it is a "what shipped" record rather
    than work in flight, so it can be hidden (env or plugin config file)."""
    val = os.environ.get("FM_FLOW_SHOW_LANDED")
    if val is None:
        try:
            with open(os.path.join(_plugin_config_dir(), "show_landed")) as fh:
                val = fh.read().strip()
        except OSError:
            val = ""
    return (val or "1").strip().lower() not in ("0", "false", "no", "off")


def set_show_landed_pref(show: bool) -> None:
    """Persist landed-column visibility for this and later board sessions."""
    os.environ["FM_FLOW_SHOW_LANDED"] = "1" if show else "0"
    path = os.path.join(_plugin_config_dir(), "show_landed")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("1\n" if show else "0\n")
    except OSError:
        pass


def board_ticket_count(cols: dict[str, list], totals: dict[str, int]) -> tuple[int, int]:
    """Return (all tickets, landed-only) using bearings totals when present."""
    landed = totals.get("landed", len(cols.get("landed") or []))
    total = sum(totals.get(key, len(cols.get(key) or [])) for key, _ in COLUMNS)
    return total, landed


# Crew tab that merges every mate board (planned + running only; no Landed).
ALL_CREW_LABEL = "All"
ACTIVE_BUCKETS = tuple(key for key, _ in COLUMNS if key != "landed")


def is_aggregate_home(home: Home | None) -> bool:
    return home is not None and home.kind == "all"


def real_homes(homes: list[Home]) -> list[Home]:
    return [h for h in homes if not is_aggregate_home(h)]


def inject_all_crew(homes: list[Home]) -> list[Home]:
    if not homes or any(h.label == ALL_CREW_LABEL for h in homes):
        return homes
    return [Home(ALL_CREW_LABEL, "", "all")] + homes


def fleet_planned_running_count(
    homes: list[Home], counts: dict[str, int], landed_counts: dict[str, int]
) -> int | None:
    """Tickets in Charted/Underway/Captain's Call/Awaiting Merge across the fleet."""
    total = 0
    seen = False
    for home in real_homes(homes):
        count = counts.get(home.label)
        if count is None:
            continue
        seen = True
        total += max(0, count - landed_counts.get(home.label, 0))
    return total if seen else None


def merge_fleet_columns(
    parts: list[tuple[Home, dict[str, list[Card]], dict[str, int]]],
) -> tuple[dict[str, list[Card]], dict[str, int]]:
    """Union every mate's active columns into one board (no Landed rows)."""
    cols: dict[str, list[Card]] = {key: [] for key, _ in COLUMNS}
    totals: dict[str, int] = {key: 0 for key, _ in COLUMNS}
    order = {h.label: i for i, h in enumerate(real_homes([h for h, _, _ in parts]))}

    def sort_key(card: Card) -> tuple:
        label = next((h.label for h, _, _ in parts if h.path == card.home_path), "")
        return (order.get(label, 99), card.task)

    for home, home_cols, home_totals in parts:
        for key in ACTIVE_BUCKETS:
            bucket = list(home_cols.get(key) or [])
            cols[key].extend(bucket)
            totals[key] += home_totals.get(key, len(bucket))
        totals["landed"] += home_totals.get("landed", len(home_cols.get("landed") or []))

    for key in ACTIVE_BUCKETS:
        cols[key].sort(key=sort_key)
    return cols, totals


# ----------------------------------------------------------------------------
# home discovery
# ----------------------------------------------------------------------------


class Home:
    def __init__(self, label: str, path: str, kind: str, mate_id: str = ""):
        self.label = label
        self.path = path
        self.kind = kind  # "captain" | "secondmate" | "other"
        # The treehouse lease holder (the mate id the snapshot's owner column
        # uses), kept even when a presentation label overrides the display name.
        self.mate_id = mate_id

    def __repr__(self) -> str:
        return f"Home({self.label!r}, {self.path!r}, {self.kind!r})"


def _plugin_config_dir() -> str:
    base = os.environ.get("HERDR_CONFIG_DIR") or os.path.expanduser("~/.config/herdr")
    return os.path.join(base, "plugins", "config", "herdr-firstmate-flow")


def _is_firstmate_home(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "bin", "fm-bearings-snapshot.sh"))


def _has_task_dirs(path: str) -> bool:
    data = os.path.join(path, "data")
    try:
        with os.scandir(data) as it:
            for e in it:
                if e.is_dir() and not e.name.startswith("."):
                    return True
                if e.is_file() and e.name.endswith(".md") and not NON_TASK.match(e.name):
                    return True
    except OSError:
        return False
    return False


def _lease_holders(treehouse_root: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for state_file in glob.glob(os.path.join(treehouse_root, "*", "treehouse-state.json")):
        try:
            with open(state_file) as fh:
                data = json.load(fh)
        except Exception:
            continue
        for wt in data.get("worktrees", []):
            holder = wt.get("lease_holder")
            path = wt.get("path")
            if holder and path:
                out[os.path.realpath(path)] = holder
    return out


def _presentation_labels() -> dict[str, str]:
    """home path -> parent_label from any .herdr-presentation file in it."""
    out: dict[str, str] = {}
    for state in glob.glob(os.path.expanduser("~/.treehouse/*/*/firstmate/state")):
        home = os.path.dirname(state)
        try:
            names = os.listdir(state)
        except OSError:
            continue
        for name in names[:200]:
            if not name.endswith(".herdr-presentation"):
                continue
            try:
                with open(os.path.join(state, name)) as fh:
                    for line in fh:
                        if line.startswith("parent_label=") and "parent_label" not in out:
                            out[home] = line.split("=", 1)[1].strip()
                            break
                if home in out:
                    break
            except OSError:
                pass
    return out


def _home_label(home: Home, leases: dict[str, str], pres: dict[str, str]) -> str:
    if home.kind == "captain":
        return "captain"
    rel = os.path.realpath(home.path)
    if rel in pres:
        return pres[rel]
    if rel in leases:
        return f"2ndmate-{leases[rel]}"
    return os.path.basename(os.path.dirname(home.path)) or "mate"


def _live_agent_homes() -> set[str]:
    """Set of cwds where a live Herdr agent runs (used to keep fresh mates)."""
    agents, _ = herdr_agents()
    return {os.path.realpath(a.get("cwd", "")) for a in agents.values() if a.get("cwd")}


def discover_homes(verbose: bool = False) -> list[Home]:
    candidates: list[str] = []

    # explicit config: label=path lines
    cfg = os.path.join(_plugin_config_dir(), "homes.conf")
    explicit: list[tuple[str, str]] = []
    if os.path.isfile(cfg):
        try:
            with open(cfg) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    label, path = line.split("=", 1)
                    explicit.append((label.strip(), os.path.expanduser(path.strip())))
        except OSError:
            pass

    env_homes = os.environ.get("FM_FLOW_HOMES", "").strip()
    if env_homes:
        for item in env_homes.replace(",", " ").split():
            if "=" in item:
                label, path = item.split("=", 1)
                explicit.append((label.strip(), os.path.expanduser(path.strip())))

    single = os.environ.get("FM_HOME", "").strip()
    if not single:
        fm_home_file = os.path.join(_plugin_config_dir(), "fm_home")
        try:
            with open(fm_home_file) as fh:
                single = fh.read().strip()
        except OSError:
            single = ""
    if single:
        candidates.append(os.path.expanduser(single))

    candidates.append(os.path.expanduser("~/firstmate"))
    treehouse_paths = {
        os.path.realpath(p)
        for p in glob.glob(os.path.expanduser("~/.treehouse/*/*/firstmate"))
    }
    candidates.extend(sorted(treehouse_paths))

    leases = _lease_holders(os.path.expanduser("~/.treehouse"))
    pres = _presentation_labels()
    try:
        live_homes = _live_agent_homes()
    except Exception:
        live_homes = set()

    homes: list[Home] = []
    seen: set[str] = set()

    def add(
        path: str, forced_label: str | None = None, require_assignment: bool = False
    ) -> None:
        path = os.path.realpath(os.path.expanduser(path))
        if path in seen or not _is_firstmate_home(path):
            return
        # An unleased treehouse worktree is a spare slot, not a crew: no mate
        # was assigned to it, so a bare worktree number stays out of the tabs.
        if (
            require_assignment
            and path not in leases
            and path not in pres
            and path not in live_homes
        ):
            return
        seen.add(path)
        has_captain = os.path.isfile(os.path.join(path, "data", "captain.md"))
        kind = "captain" if has_captain else "secondmate"
        if not _has_task_dirs(path) and path not in live_homes:
            return
        home = Home("", path, kind, mate_id=leases.get(path, ""))
        home.label = forced_label or _home_label(home, leases, pres)
        homes.append(home)

    for label, path in explicit:
        add(path, forced_label=label)
    configured = {os.path.realpath(os.path.expanduser(p)) for _, p in explicit}
    if single:
        configured.add(os.path.realpath(os.path.expanduser(single)))
    for path in candidates:
        real = os.path.realpath(os.path.expanduser(path))
        add(path, require_assignment=real in treehouse_paths and real not in configured)

    homes.sort(key=lambda h: (h.kind != "captain", h.label))
    homes = inject_all_crew(homes)
    if verbose:
        for h in homes:
            print(f"{h.label:32s} {h.path}  [{h.kind}]")
    return homes


# ----------------------------------------------------------------------------
# herdr + firstmate data collection
# ----------------------------------------------------------------------------


def _herdr_bin() -> str:
    """Resolve the herdr binary. Plugin panes get a minimal PATH that usually
    lacks ~/.local/bin, so HERDR_BIN_PATH and explicit fallbacks matter."""
    for candidate in (
        os.environ.get("HERDR_BIN_PATH"),
        shutil.which("herdr"),
        os.path.expanduser("~/.local/bin/herdr"),
        "/usr/local/bin/herdr",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return "herdr"


HERDR_BIN = _herdr_bin()


def _child_env() -> dict:
    """Environment for child processes: keep the pane env, but make sure the
    usual user bin directories are on PATH (Firstmate scripts and herdr live
    there, while plugin panes inherit a minimal PATH)."""
    env = dict(os.environ)
    parts = [p for p in (env.get("PATH") or "").split(":") if p]
    for extra in (os.path.expanduser("~/.local/bin"), "/usr/local/bin"):
        if extra not in parts:
            parts.append(extra)
    env["PATH"] = ":".join(parts)
    env.setdefault("HERDR_BIN_PATH", HERDR_BIN)
    return env


def _run(cmd: list[str], timeout: float = 25.0) -> str | None:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_child_env(),
            stdin=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            _debug(f"run failed rc={p.returncode}: {' '.join(cmd[:3])} {p.stderr.strip()[:120]}")
            return None
        return p.stdout
    except Exception as exc:
        _debug(f"run error: {' '.join(cmd[:3])} {exc!r}")
        return None


def herdr_agents() -> tuple[dict[str, dict], dict[str, dict]]:
    """pane_id -> agent info, pane_id -> pane info (always live from Herdr)."""
    agents: dict[str, dict] = {}
    panes: dict[str, dict] = {}
    out = _run([HERDR_BIN, "agent", "list"])
    if out:
        try:
            data = json.loads(out)
            for a in data.get("result", {}).get("agents", []):
                pid = a.get("pane_id")
                if pid:
                    agents[pid] = a
        except Exception:
            pass
    out = _run([HERDR_BIN, "pane", "list"])
    if out:
        try:
            data = json.loads(out)
            for p in data.get("result", {}).get("panes", []):
                pid = p.get("pane_id")
                if pid:
                    panes[pid] = p
        except Exception:
            pass
    return agents, panes


_META_CACHE: dict[str, tuple[float, dict]] = {}


def read_meta(path: str) -> dict:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _META_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    meta: dict[str, str] = {}
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    meta[k.strip()] = v.strip()
    except OSError:
        pass
    _META_CACHE[path] = (mtime, meta)
    return meta


def read_status_tail(path: str) -> tuple[str, str, int]:
    """Return (state, text, at_epoch) from the last status line."""
    try:
        size = os.path.getsize(path)
        with open(path, errors="replace") as fh:
            if size > 8192:
                fh.seek(size - 8192)
                fh.readline()
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return "", "", 0
    if not lines:
        return "", "", 0
    line = lines[-1]
    m = re.match(r"^(?P<state>[a-zA-Z_-]+)(?:\s+\[at=(?P<at>\d+)\])?\s*:?\s*(?P<text>.*)$", line)
    if not m:
        return "", line[:200], 0
    return m.group("state"), m.group("text")[:200], _int(m.group("at") or 0)


def build_meta_index(homes: list[Home]) -> dict[str, tuple[str, str]]:
    """task_id -> (meta_path, home_path) across all homes."""
    index: dict[str, tuple[str, str]] = {}
    for home in homes:
        state = os.path.join(home.path, "state")
        try:
            names = os.listdir(state)
        except OSError:
            continue
        for name in names:
            if name.endswith(".meta"):
                task = name[: -len(".meta")]
                index.setdefault(task, (os.path.join(state, name), home.path))
    return index


def bearings_flags() -> list[str]:
    """Follow Firstmate's own bounds by default (in_flight 20, gates 20,
    landed 6 newest per home). FM_FLOW_ALL=1 asks for every row instead."""
    flags = ["--json", "--fields", "paths"]
    if _on("FM_FLOW_ALL"):
        flags += ["--all-in-flight", "--all-queued", "--all-landed"]
    return flags


def bearings_snapshot(home: Home) -> dict:
    bin_path = os.path.join(home.path, "bin", "fm-bearings-snapshot.sh")
    env = _child_env()
    env["FM_HOME"] = home.path
    try:
        p = subprocess.run(
            [bin_path, *bearings_flags()],
            capture_output=True,
            text=True,
            timeout=25,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            return {}
        return json.loads(p.stdout)
    except Exception:
        return {}


def _strip_owner(task_id: str) -> str:
    return task_id.split("/", 1)[1] if "/" in task_id else task_id


# ----------------------------------------------------------------------------
# Captain's Call decision cards
# ----------------------------------------------------------------------------

_BOARD_CARD_CACHE: dict[str, tuple[tuple, dict[str, dict]]] = {}


def _cards_from_json(data: object) -> dict[str, dict]:
    """key -> card for the captains_call of a board payload."""
    out: dict[str, dict] = {}
    if not isinstance(data, dict):
        return out
    for item in data.get("captains_call") or []:
        if isinstance(item, dict) and isinstance(item.get("key"), str) and item["key"]:
            out[item["key"]] = item
    return out


def _cached_cards(path: str, extract) -> dict[str, dict]:
    """Parse one card source, cached by mtime+size so a 2s tick never
    re-parses a board file that has not changed."""
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    hit = _BOARD_CARD_CACHE.get(path)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        cards = extract(text)
    except Exception as exc:
        _debug(f"decision card read failed for {path}: {exc!r}")
        cards = {}
    _BOARD_CARD_CACHE[path] = (sig, cards)
    return cards


def _board_file_cards(path: str) -> dict[str, dict]:
    """The captains_call payload of a live Lavish board (the board file is the
    post-build artifact, so it carries the injected reconcile option)."""

    def extract(text: str) -> dict[str, dict]:
        m = re.search(
            r'<script[^>]*\bid="bearings-data"[^>]*>(.*?)</script>', text, re.S
        )
        if not m:
            return {}
        try:
            return _cards_from_json(json.loads(m.group(1)))
        except (ValueError, TypeError):
            return {}

    return _cached_cards(path, extract)


def _payload_file_cards(path: str) -> dict[str, dict]:
    """A composed data/bearings-board-payload-*.json (pre-build: no reconcile
    option, so the dialog injects the standard one itself)."""

    def extract(text: str) -> dict[str, dict]:
        try:
            return _cards_from_json(json.loads(text))
        except (ValueError, TypeError):
            return {}

    return _cached_cards(path, extract)


def _store_card(path: str) -> dict:
    """One durable state/decision-cards/<task>.json record (schema
    fm-decision-card.v1), cached by mtime+size like the other card sources."""
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    hit = _BOARD_CARD_CACHE.get(path)
    if hit is not None and hit[0] == sig:
        cached = hit[1].get("__card__")
        return cached if isinstance(cached, dict) else {}
    card: dict = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.loads(fh.read())
        inner = data.get("card") if isinstance(data, dict) else None
        if isinstance(inner, dict):
            card = inner
    except Exception as exc:
        _debug(f"decision card store read failed for {path}: {exc!r}")
        card = {}
    _BOARD_CARD_CACHE[path] = (sig, {"__card__": card})
    return card


def decision_card_for(key: str, *homes: Home | None) -> dict:
    """The composed board card for one captain-held task id.

    The live board in a home is authoritative, the durable decision-card store
    written by the board build survives a rebuild, and the composed payload
    files it was built from are the last fallback. The owning home wins over
    the board on screen, because a parent board can carry a secondmate's card.
    """
    ordered: list[Home] = []
    for home in homes:
        if home is not None and all(home.path != h.path for h in ordered):
            ordered.append(home)
    # every card source is keyed by a validated slug; never build a path from
    # anything else
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key):
        return {}
    for home in ordered:
        card = _board_file_cards(
            os.path.join(home.path, ".lavish", "bearings-board.html")
        ).get(key)
        if card:
            return card
    for home in ordered:
        card = _store_card(
            os.path.join(home.path, "state", "decision-cards", f"{key}.json")
        )
        if card:
            return card
    for home in ordered:
        for path in sorted(
            glob.glob(os.path.join(home.path, "data", "bearings-board-payload-*.json")),
            reverse=True,
        ):
            card = _payload_file_cards(path).get(key)
            if card:
                return card
    return {}


def resolve_owner_home(
    homes: list[Home] | None, owner: str, active: Home | None
) -> Home | None:
    """The home whose backlog owns a Captain's Call row.

    The snapshot marks a home's own holds ``(main)`` and a secondmate's with
    the mate id, which the discovered crew labels carry (``2ndmate-<id>``).
    """
    if not owner or owner == "(main)":
        return active
    for home in homes or []:
        if home.mate_id == owner:
            return home
    for home in homes or []:
        if home.label == owner:
            return home
    for home in homes or []:
        if home.label.endswith("-" + owner) or os.path.basename(home.path) == owner:
            return home
    return None


def resolve_owner_lane(homes: list[Home] | None, owner: str) -> tuple[Home | None, str]:
    """The parent home and lane id that can wake a mate-owned call's agent.

    The parent home is the one whose state directory records the mate lane
    (state/<owner>.meta); a home-owned ``(main)`` row has no lane to steer.
    """
    if not owner or owner == "(main)":
        return None, ""
    for home in homes or []:
        if os.path.isfile(os.path.join(home.path, "state", f"{owner}.meta")):
            return home, owner
    return None, ""


def _decision_lookup_homes(
    homes: list[Home] | None, owner_home: Home | None, active: Home | None
) -> list[Home]:
    """Homes to search for a composed Captain's Call card, owner first.

    The /bearings lavish fleet board and its decision-card store live on the
    captain home even when the call's backlog owner is a secondmate, so every
    discovered home must be searched — not only the tab on screen.
    """
    ordered: list[Home] = []
    for home in (owner_home, *((homes or [])), active):
        if home is not None and all(home.path != h.path for h in ordered):
            ordered.append(home)
    return ordered


def decision_card_content(
    card: Card, homes: list[Home] | None, active: Home | None
) -> dict:
    """Normalized dialog content for a Captain's Call ticket.

    With no composed card (no board yet, or a card not composed for this key),
    the ticket's durable title and hold reason become the title and about line,
    and the captain answers in their own words - or sends Reconcile to have the
    call re-checked.
    """
    owner_home = resolve_owner_home(homes, card.owner, active)
    raw = decision_card_for(card.task, *_decision_lookup_homes(homes, owner_home, active))
    ctype = (str(raw.get("type") or "decision").strip() or "decision")
    options: list[dict[str, str]] = []
    for opt in raw.get("options") or []:
        if not isinstance(opt, dict):
            continue
        value = opt.get("value")
        if not isinstance(value, str) or not value:
            continue
        options.append(
            {
                "value": value,
                "label": str(opt.get("label") or value),
                "hint": str(opt.get("hint") or ""),
            }
        )
    # The board's build injects the standard reconcile choice on every decision
    # card; do the same for a raw payload or a card-less ticket.
    if ctype == "decision" and all(o["value"] != "reconcile" for o in options):
        options.append(dict(RECONCILE_OPTION))
    recommend = raw.get("recommend_value")
    repo = str(raw.get("repo") or "")
    if not repo and card.owner and card.owner != "(main)":
        repo = card.owner
    title = str(raw.get("title") or "")
    about = str(raw.get("about") or "")
    decide = str(raw.get("decide") or "")
    wake_home, wake_lane = resolve_owner_lane(homes, card.owner)
    if not raw:
        # No composed card. Prefer the snapshot's durable title and hold reason;
        # an older snapshot only carries the collapsed "<title>: <reason>"
        # summary, which is split as a fallback.
        summary = str(card.title or "").strip()
        reason = str(card.doing or "").strip()
        if reason in ("", "captain-hold", "decision", "hold"):
            reason = ""
        if reason:
            title, about = summary, reason
        else:
            head, sep, tail = summary.partition(": ")
            if sep and head:
                title, about = head, tail
            else:
                title, about = summary, ""
        title = title or card.task
        # a truncated reason stub is noise, not context
        if about.endswith("\u2026") and len(about) < 24:
            about = ""
        decide = "Answer in your own words, or send Reconcile to re-check the latest state."
    elif not title:
        title = str(card.title or card.task)
    if not decide:
        decide = "Pick an option below, or answer in your own words."
    return {
        "key": card.task,
        "title": title,
        "type": ctype,
        "repo": repo,
        "about": about,
        "decide": decide,
        "detail": str(raw.get("detail") or ""),
        "options": options,
        "recommend": recommend if isinstance(recommend, str) else "",
        "freeform": bool(raw.get("allow_freeform")) if raw else True,
        "freeform_hint": str(
            raw.get("freeform_hint") or "or answer in your own words\u2026"
        ),
        "close": raw.get("close") if raw.get("close") in ("done", "release") else "",
        "home_path": owner_home.path if owner_home is not None else "",
        "wake_home": wake_home.path if wake_home is not None else "",
        "wake_lane": wake_lane,
    }


class DecisionDialog:
    """One Captain's Call card open for a decision (the board card as a modal)."""

    __slots__ = (
        "key",
        "title",
        "ctype",
        "repo",
        "about",
        "decide",
        "detail",
        "options",
        "recommend",
        "freeform",
        "freeform_hint",
        "close",
        "home_path",
        "wake_home",
        "wake_lane",
        "cursor",
        "selected",
        "note",
        "focus",
        "busy",
        "queued",
        "error",
        "done_at",
    )

    def __init__(self, content: dict) -> None:
        self.key = content["key"]
        self.title = content["title"]
        self.ctype = content["type"]
        self.repo = content["repo"]
        self.about = content["about"]
        self.decide = content["decide"]
        self.detail = content["detail"]
        self.options = content["options"]
        self.recommend = content["recommend"]
        self.freeform = content["freeform"]
        self.freeform_hint = content["freeform_hint"]
        self.close = content["close"]
        self.home_path = content["home_path"]
        self.wake_home = content["wake_home"]
        self.wake_lane = content["wake_lane"]
        self.cursor = 0
        # The board preselects the recommended option; without one the captain
        # starts on the freeform row.
        self.selected = next(
            (
                i
                for i, opt in enumerate(self.options)
                if self.recommend and opt["value"] == self.recommend
            ),
            -1,
        )
        self.note = ""
        self.focus = "options"
        self.busy = False
        self.queued = False
        self.error = ""
        self.done_at = 0.0

    def selected_option(self) -> dict[str, str] | None:
        if 0 <= self.selected < len(self.options):
            return self.options[self.selected]
        return None

    def display_answer(self) -> str:
        """What the captain chose, exactly as the board shows it: the selected
        option plus a typed note, or just the note when no option is selected."""
        option = self.selected_option()
        value = option["value"] if option else ""
        note = self.note.strip()
        if value and note:
            return value + " - " + note
        return value or note

    def keyed_line(self) -> str:
        """The keyed-answer line: key, answer, label, optional card close mode.

        The board's captured prompt carries the note beside the selection; this
        channel has only the keyed line, so the note rides the answer and the
        captain's full words reach the durable decision.
        """
        answer = self.display_answer()
        line = f"{self.key}\t{answer}\t{self.title} -> {answer}"
        if self.close:
            line += f"\t{self.close}"
        return line

    def reconcile_line(self) -> str:
        """A reconcile request row: the task id and the note as provenance."""
        note = self.note.strip()
        return f"{self.key}\t{note}" if note else self.key


def _hold_script(home_path: str) -> str:
    return os.path.join(home_path, "bin", "fm-captain-hold.sh")


def _run_captain_hold(home_path: str, args: list[str], stdin_text: str) -> tuple[bool, str]:
    """Run the firstmate answer intake in the home that owns the task."""
    script = _hold_script(home_path)
    if not os.path.isfile(script):
        return False, f"no fm-captain-hold.sh in {home_path}"
    env = _child_env()
    env["FM_HOME"] = home_path
    try:
        p = subprocess.run(
            [script, *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except Exception as exc:
        _debug(f"captain hold failed: {exc!r}")
        return False, f"could not run the answer intake: {exc!r}"
    lines = [ln for ln in ((p.stdout or "") + (p.stderr or "")).splitlines() if ln.strip()]
    detail = lines[-1].strip() if lines else ""
    return p.returncode == 0, detail


def ensure_decision_binding(home_path: str) -> tuple[bool, str]:
    """Reconcile requests need a bound captured source. Binding is a deliberate
    opt-in record the channel makes on its first reconcile, and `bind` does not
    require the source to exist anywhere else."""
    script = _hold_script(home_path)
    if not os.path.isfile(script):
        return False, f"no fm-captain-hold.sh in {home_path}"
    env = _child_env()
    env["FM_HOME"] = home_path
    try:
        p = subprocess.run(
            [script, "binding", DECISION_SOURCE_ID],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        if p.returncode == 0 and (p.stdout or "").strip():
            return True, ""
    except Exception as exc:
        return False, f"could not read the decision binding: {exc!r}"
    return _run_captain_hold(home_path, ["bind", DECISION_SOURCE_ID], "")


def wake_owner(dialog: DecisionDialog) -> str:
    """Steer the owning agent through the parent home's lane inbox.

    The record is durable, but the wake is what makes someone act on it. Returns
    an empty string on success (or when there is no lane to wake) and a one-line
    warning the dialog can show otherwise.
    """
    if not dialog.wake_home or not dialog.wake_lane:
        return ""
    script = os.path.join(dialog.wake_home, "bin", "fm-send.sh")
    if not os.path.isfile(script):
        return f"wake skipped: no fm-send.sh in {dialog.wake_home}"
    option = dialog.selected_option()
    if option and option["value"] == "reconcile":
        text = (
            f"captain's deck reconcile request for {dialog.key}: the captain sent this call "
            "back for a re-check. Run bin/fm-captain-hold.sh reconcile list, then retire it "
            "with reconcile close --evidence-file when the premise is moot, or reconcile note "
            "--note-file when the call is still active. Reconcile is not the captain's words: "
            "never use answer for it."
        )
    else:
        text = (
            f"captain's deck answer for {dialog.key}: {dialog.display_answer()}. The keyed-answer "
            "intake already recorded it; act on it - resume released work, or confirm the call "
            "is closed."
        )
    delivery = hashlib.sha256(f"{dialog.key}\t{text}".encode("utf-8")).hexdigest()[:16]
    env = _child_env()
    env["FM_HOME"] = dialog.wake_home
    try:
        p = subprocess.run(
            [script, dialog.wake_lane, "--fire-and-forget", delivery, text],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except Exception as exc:
        _debug(f"owner wake failed: {exc!r}")
        return f"answer recorded, but the owner wake failed: {exc!r}"
    if p.returncode == 0:
        return ""
    lines = [ln for ln in ((p.stderr or "") + (p.stdout or "")).splitlines() if ln.strip()]
    detail = lines[-1].strip() if lines else "fm-send refused"
    _debug(f"owner wake refused: {detail}")
    return f"answer recorded, but the owner was not woken: {detail}"


def run_submit(dialog: DecisionDialog) -> tuple[bool, str]:
    """Feed the captain's choice to firstmate's intake, then wake the owning
    agent the way the other channels do. Reconcile takes the separate
    reconcile-request path; everything else is a keyed answer."""
    if not dialog.home_path:
        return False, (
            f"cannot tell which home owns {dialog.key}; "
            "answer it from the Lavish board or chat"
        )
    option = dialog.selected_option()
    if option and option["value"] == "reconcile":
        ok, detail = ensure_decision_binding(dialog.home_path)
        if not ok:
            return False, detail
        ok, detail = _run_captain_hold(
            dialog.home_path,
            [
                "reconcile-requests",
                "--source-id",
                DECISION_SOURCE_ID,
                "--source",
                DECISION_SOURCE,
            ],
            dialog.reconcile_line() + "\n",
        )
    else:
        ok, detail = _run_captain_hold(
            dialog.home_path,
            ["answers", "--source", DECISION_SOURCE],
            dialog.keyed_line() + "\n",
        )
    if ok:
        # A wake failure never reverses the recorded answer, so it is reported
        # beside the queued state instead of failing the submit.
        wake_detail = wake_owner(dialog)
        if wake_detail:
            detail = f"{detail} | {wake_detail}" if detail else wake_detail
    return ok, detail


class Card:
    __slots__ = (
        "id",
        "task",
        "bucket",
        "title",
        "badge",
        "badge_color",
        "doing",
        "agent",
        "model",
        "effort",
        "kind",
        "mode",
        "worktree",
        "branch",
        "pane_id",
        "workspace_id",
        "tab_id",
        "artifact",
        "blocked_by",
        "live_status",
        "status_text",
        "owner",
        "home_path",
    )


def _trunc(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "\u2026"


def make_cards(
    home: Home,
    snap: dict,
    meta_index: dict[str, tuple[str, str]],
    agents: dict[str, dict],
    panes: dict[str, dict],
    homes: list[Home] | None = None,
) -> tuple[dict[str, list[Card]], dict[str, int]]:
    """Bucket snapshot entries into the five columns.

    Returns ``(columns, totals)`` where totals counts the entries per column
    before the Landed cap, so the UI can show e.g. ``Landed (10/18)``.
    """
    cols: dict[str, list[Card]] = {key: [] for key, _ in COLUMNS}
    totals: dict[str, int] = {key: 0 for key, _ in COLUMNS}

    paths: dict[str, dict] = {}
    for p in snap.get("paths") or []:
        if isinstance(p, dict) and p.get("id"):
            paths[p["id"]] = p
            paths.setdefault(_strip_owner(p["id"]), p)

    def populate(card: Card, entry: dict) -> None:
        raw_id = entry.get("id") or entry.get("key") or "?"
        card.id = raw_id
        card.task = _strip_owner(raw_id)
        card.kind = (entry.get("kind") or "").strip()
        card.blocked_by = (entry.get("blocked_by") or "").strip()
        card.artifact = (entry.get("artifact") or "").strip()
        card.owner = ""
        card.home_path = home.path

        p = paths.get(raw_id) or paths.get(card.task) or {}
        card.worktree = p.get("worktree") or ""

        meta_path, meta_home = meta_index.get(card.task, ("", ""))
        meta = read_meta(meta_path) if meta_path else {}
        card.agent = meta.get("harness", "")
        card.model = meta.get("model", "")
        card.effort = meta.get("effort", "")
        card.mode = meta.get("mode", "")
        card.branch = meta.get("branch", "")
        if not card.worktree:
            card.worktree = meta.get("worktree", "")
        card.pane_id = meta.get("herdr_pane_id", "")
        card.workspace_id = meta.get("herdr_workspace_id", "")
        card.tab_id = meta.get("herdr_tab_id", "")

        # stale meta: verify against live panes
        if card.pane_id and card.pane_id not in panes:
            fallback = ""
            for pid, pane in panes.items():
                cwds = {os.path.realpath(pane.get("cwd") or ""), os.path.realpath(pane.get("foreground_cwd") or "")}
                if card.worktree and os.path.realpath(card.worktree) in cwds:
                    fallback = pid
                    break
            card.pane_id = fallback or card.pane_id

        live = agents.get(card.pane_id) if card.pane_id else None
        card.live_status = (live or {}).get("agent_status", "")
        if live and not card.agent:
            card.agent = live.get("agent", "")

        status_file = p.get("status") or os.path.join(meta_home or home.path, "state", f"{card.task}.status")
        st_state, st_text, _ = read_status_tail(status_file) if status_file else ("", "", 0)
        card.status_text = st_text

        # ---- badge ----
        doing = (entry.get("doing") or "").lower()
        state = (entry.get("state") or "").lower()
        if card.bucket == "charted":
            if card.blocked_by and card.blocked_by != "-":
                card.badge, card.badge_color = "\u26d4 blocked", C_BAD
            else:
                card.badge, card.badge_color = "\u00b7 queued", C_QUEUE
        elif card.bucket == "captains_call":
            card.badge, card.badge_color = "\u2691 captain", C_DECIDE
        elif card.bucket == "landed":
            card.badge, card.badge_color = "\u2713 landed", C_OK
        else:
            if card.live_status == "blocked":
                card.badge, card.badge_color = "\u26d4 blocked", C_BAD
            elif st_state == "needs-decision":
                card.badge, card.badge_color = "\u2691 decision", C_DECIDE
            elif card.bucket == "awaiting_merge":
                # Firstmate says the crew is done; the card belongs to Awaiting
                # Merge even if the pane is busy again (e.g. merge follow-up).
                card.badge, card.badge_color = "\u25cd awaits merge", C_REVIEW
            elif "validating" in doing:
                card.badge, card.badge_color = "\u25d0 validating", C_WARN
            elif card.live_status == "working" or "harness busy" in doing:
                card.badge, card.badge_color = "\u25cf working", C_OK
            elif state == "done":
                card.badge, card.badge_color = "\u25cd awaits merge", C_REVIEW
            elif state == "parked":
                card.badge, card.badge_color = "\u23f8 parked", C_QUEUE
            elif state == "paused":
                card.badge, card.badge_color = "\u23f8 paused", C_QUEUE
            elif state == "failed":
                card.badge, card.badge_color = "\u26d4 failed", C_BAD
            elif card.live_status in ("idle", "done"):
                card.badge, card.badge_color = ("\u25cb idle" if card.live_status == "idle" else "\u2713 done"), C_DIM
            else:
                card.badge, card.badge_color = "\u00b7 queued", C_QUEUE

        # a queued card with a live working pane is really underway
        if card.bucket == "charted" and card.live_status in ("working", "blocked"):
            card.bucket = "underway"
            card.badge, card.badge_color = (
                ("\u26d4 blocked", C_BAD) if card.live_status == "blocked" else ("\u25cf working", C_OK)
            )

    for entry in snap.get("gates") or []:
        c = Card()
        c.bucket = "charted"
        c.title = entry.get("title") or ""
        reason = (entry.get("reason") or "").strip()
        c.doing = "" if reason in ("-", "") else reason
        populate(c, entry)
        cols["charted"].append(c)
        totals["charted"] += 1

    for entry in snap.get("in_flight") or []:
        c = Card()
        state = (entry.get("state") or "").lower()
        # Firstmate keeps every in-flight row in Underway; only a finished crew
        # (state done) is projected into Awaiting Merge. parked/paused/failed
        # stay in Underway and carry their state as a badge.
        c.bucket = "awaiting_merge" if state == "done" else "underway"
        c.title = entry.get("name") or ""
        c.doing = (entry.get("doing") or "").strip()
        populate(c, entry)
        cols[c.bucket].append(c)
        totals[c.bucket] += 1

    for entry in snap.get("decisions_open") or []:
        c = Card()
        c.bucket = "captains_call"
        # the durable title and hold reason arrive beside the collapsed summary,
        # so a ticket with no composed card still has real context
        c.title = entry.get("title") or entry.get("summary") or ""
        reason = (entry.get("reason") or "").strip()
        c.doing = reason or (entry.get("verb") or "").strip()
        populate(c, entry)
        # The row's owner names the home whose backlog holds the call, so the
        # answer runs the intake in that home rather than the board on screen.
        c.owner = (entry.get("owner") or "").strip()
        owner_home = resolve_owner_home(homes, c.owner, home)
        c.home_path = owner_home.path if owner_home is not None else ""
        cols["captains_call"].append(c)
        totals["captains_call"] += 1

    landed_entries = snap.get("landed") or []
    totals["landed"] = len(landed_entries)
    for entry in landed_entries[:LANDED_LIMIT] if LANDED_LIMIT else landed_entries:
        c = Card()
        c.bucket = "landed"
        c.title = entry.get("what") or ""
        c.doing = ""
        populate(c, entry)
        cols["landed"].append(c)

    return cols, totals


# ----------------------------------------------------------------------------
# collector thread
# ----------------------------------------------------------------------------


class Snapshot:
    def __init__(self) -> None:
        self.seq = 0
        self.home: Home | None = None
        self.cols: dict[str, list[Card]] = {k: [] for k, _ in COLUMNS}
        self.collected_at = 0.0
        self.homes: list[Home] = []
        self.counts: dict[str, int] = {}
        self.landed_counts: dict[str, int] = {}
        self.totals: dict[str, int] = {}
        self.activity: dict[str, str] = {}
        self.loading = False
        self.error = ""
        self.agents: dict[str, dict] = {}
        self.panes: dict[str, dict] = {}


class Collector(threading.Thread):
    """Two-tier collector.

    Every tick (``FM_FLOW_TICK_SECS``) it rebuilds the cards of the active home
    from cheap sources: Herdr's live agent/pane list plus each task's
    ``state/<id>.meta`` and the tail of ``state/<id>.status`` (both cached by
    mtime). The expensive bearings snapshot only runs when it is older than
    ``FM_FLOW_BEARINGS_SECS``, when the crew switches to an uncached home, or on
    an explicit refresh. Only the active home is rebuilt, so other crews do not
    cost anything while you look at one board.
    """

    def __init__(self, active_label: str = "") -> None:
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self._snap = Snapshot()
        self._active = active_label
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._force = False
        self._last_discover = 0.0
        self._raw: dict[str, tuple[dict, float, str]] = {}
        self._meta_index: dict[str, tuple[str, str]] = {}
        self._meta_at = 0.0
        self._counts: dict[str, int] = {}
        self._landed_counts: dict[str, int] = {}

    # -- public API ---------------------------------------------------------
    def snapshot(self) -> Snapshot:
        with self.lock:
            return self._snap

    def set_active(self, label: str) -> None:
        self._active = label
        self._wake.set()

    def refresh_now(self) -> None:
        self._force = True
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- internals ----------------------------------------------------------
    def _discover(self, force: bool = False) -> list[Home]:
        now = time.time()
        if force or now - self._last_discover > HOMES_REDISCOVER_SECS:
            self._last_discover = now
            homes = discover_homes()
            if homes:
                return homes
        with self.lock:
            return self._snap.homes or discover_homes()

    def _publish(self, snap: Snapshot) -> None:
        with self.lock:
            snap.seq = self._snap.seq + 1
            self._snap = snap

    @staticmethod
    def _activity(homes: list[Home], agents: dict[str, dict]) -> dict[str, str]:
        live: dict[str, str] = {}
        for a in agents.values():
            cwd = os.path.realpath(a.get("cwd") or "")
            if not cwd:
                continue
            status = a.get("agent_status") or ""
            if live.get(cwd) != "working" or status == "blocked":
                live[cwd] = status
        out = {
            h.label: live.get(os.path.realpath(h.path), "")
            for h in homes
            if not is_aggregate_home(h)
        }
        if any(is_aggregate_home(h) for h in homes):
            statuses = list(out.values())
            agg = ""
            if any(s == "working" for s in statuses):
                agg = "working"
            elif any(s == "blocked" for s in statuses):
                agg = "blocked"
            elif any(s for s in statuses):
                agg = next(s for s in statuses if s)
            out[ALL_CREW_LABEL] = agg
        return out

    def _bearings_stale(self, home: Home, now: float) -> bool:
        raw, ts, raw_path = self._raw.get(home.label, ({}, 0.0, ""))
        return (
            self._force
            or not raw
            or raw_path != home.path
            or now - ts > BEARINGS_TTL
        )

    def _load_bearings(
        self,
        home: Home,
        now: float,
        agents: dict[str, dict],
        panes: dict[str, dict],
        homes: list[Home],
    ) -> tuple[dict, dict[str, list[Card]], dict[str, int], bool, str]:
        """Return raw snapshot, cards, totals, loading flag, and error text."""
        raw, ts, raw_path = self._raw.get(home.label, ({}, 0.0, ""))
        stale = self._bearings_stale(home, now)
        loading = False
        if stale:
            if not raw:
                loading = True
            data = bearings_snapshot(home)
            if data:
                raw, ts, raw_path = data, time.time(), home.path
                self._raw[home.label] = (raw, ts, raw_path)
                loading = False
        if not raw:
            return {}, {k: [] for k, _ in COLUMNS}, {}, loading, f"bearings snapshot failed for {home.path}"
        cols, totals = make_cards(home, raw, self._meta_index, agents, panes, homes)
        return raw, cols, totals, loading, ""

    def _record_home_counts(self, home: Home, cols: dict[str, list[Card]], totals: dict[str, int]) -> None:
        total, landed = board_ticket_count(cols, totals)
        self._counts[home.label] = total
        self._landed_counts[home.label] = landed

    def _publish_fleet(
        self,
        homes: list[Home],
        agents: dict[str, dict],
        panes: dict[str, dict],
        now: float,
    ) -> None:
        aggregate = next(h for h in homes if is_aggregate_home(h))
        parts: list[tuple[Home, dict[str, list[Card]], dict[str, int]]] = []
        loading = False
        errors: list[str] = []
        for home in real_homes(homes):
            raw, cols, totals, home_loading, err = self._load_bearings(
                home, now, agents, panes, homes
            )
            loading = loading or home_loading
            if err:
                errors.append(err)
            if raw:
                parts.append((home, cols, totals))
                self._record_home_counts(home, cols, totals)
        cols, totals = merge_fleet_columns(parts) if parts else ({k: [] for k, _ in COLUMNS}, {})
        fleet_total = fleet_planned_running_count(homes, self._counts, self._landed_counts)
        if fleet_total is not None:
            self._counts[ALL_CREW_LABEL] = fleet_total
        error = "; ".join(errors)
        self._publish(
            self._snapshot(
                homes,
                aggregate,
                cols,
                agents,
                panes,
                totals,
                loading=loading and not parts,
                error=error,
            )
        )
        self._force = False

    def _snapshot(
        self,
        homes: list[Home],
        active: Home,
        cols: dict[str, list[Card]],
        agents: dict[str, dict],
        panes: dict[str, dict],
        totals: dict[str, int] | None = None,
        loading: bool = False,
        error: str = "",
    ) -> Snapshot:
        snap = Snapshot()
        snap.homes = homes
        snap.home = active
        snap.cols = cols
        snap.agents = agents
        snap.panes = panes
        snap.totals = totals or {}
        snap.activity = self._activity(homes, agents)
        snap.counts = dict(self._counts)
        snap.landed_counts = dict(self._landed_counts)
        snap.collected_at = time.time()
        snap.loading = loading
        snap.error = error
        return snap

    def run(self) -> None:
        _debug("collector start")
        self._discover(force=True)
        while not self._stop.is_set():
            started = time.time()
            try:
                _debug("iter: discover")
                homes = self._discover()
                _debug(f"iter: homes={len(homes)}")
                if not homes:
                    snap = Snapshot()
                    snap.error = "no Firstmate homes found (set FM_FLOW_HOMES or homes.conf)"
                    self._publish(snap)
                else:
                    active = next((h for h in homes if h.label == self._active), homes[0])
                    self._active = active.label
                    _debug(f"iter: agents for {active.label}")
                    agents, panes = herdr_agents()
                    _debug(f"iter: agents={len(agents)} panes={len(panes)}")
                    now = time.time()
                    if now - self._meta_at > 60:
                        self._meta_index = build_meta_index(homes)
                        self._meta_at = now
                    _debug(f"iter: meta={len(self._meta_index)}")

                    if is_aggregate_home(active):
                        _debug("iter: fleet board")
                        self._publish_fleet(homes, agents, panes, now)
                    else:
                        raw, ts, raw_path = self._raw.get(active.label, ({}, 0.0, ""))
                        stale = self._bearings_stale(active, now)
                        if stale:
                            if raw:
                                cached, cached_totals = make_cards(
                                    active, raw, self._meta_index, agents, panes, homes
                                )
                            else:
                                cached, cached_totals = {k: [] for k, _ in COLUMNS}, {}
                            _debug("iter: publish cached")
                            self._publish(
                                self._snapshot(
                                    homes,
                                    active,
                                    cached,
                                    agents,
                                    panes,
                                    cached_totals,
                                    loading=not raw,
                                )
                            )
                            _debug("iter: bearings begin")
                            data = bearings_snapshot(active)
                            _debug(f"iter: bearings done ok={bool(data)}")
                            if data:
                                raw, ts, raw_path = data, time.time(), active.path
                                self._raw[active.label] = (raw, ts, raw_path)

                        _debug("iter: make_cards")
                        if raw:
                            cols, totals = make_cards(
                                active, raw, self._meta_index, agents, panes, homes
                            )
                        else:
                            cols, totals = {k: [] for k, _ in COLUMNS}, {}
                        self._record_home_counts(active, cols, totals)
                        fleet_total = fleet_planned_running_count(
                            homes, self._counts, self._landed_counts
                        )
                        if fleet_total is not None:
                            self._counts[ALL_CREW_LABEL] = fleet_total
                        error = "" if raw else f"bearings snapshot failed for {active.path}"
                        _debug("iter: publish final")
                        self._publish(
                            self._snapshot(homes, active, cols, agents, panes, totals, error=error)
                        )
                        self._force = False
            except Exception as exc:  # never kill the UI thread
                _debug(f"iter: error {exc!r}")
                snap = Snapshot()
                snap.error = f"collector error: {exc}"
                self._publish(snap)
            elapsed = time.time() - started
            _debug(f"iter: end {elapsed:.2f}s")
            self._wake.wait(timeout=max(0.25, TICK_SECS - elapsed))
            self._wake.clear()


# ----------------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------------


def display_width(text: str) -> int:
    """Terminal cells used by text.

    ``len()`` is not enough: glyphs like U+26D4 NO ENTRY (the blocked badge)
    render as two-cell emoji, which would push a card's right border out of
    alignment. East Asian Wide/Fullwidth count as 2, combining marks as 0.
    """
    cells = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        cells += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return cells


def clip(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    if display_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        w = display_width(ch)
        if used + w > max(0, width - 1):
            break
        out.append(ch)
        used += w
    return "".join(out) + "\u2026"


def pad(text: str, width: int) -> str:
    text = clip(text, width)
    return text + " " * max(0, width - display_width(text))


def _wrap_long_token(token: str, width: int) -> list[str]:
    """Split a single word across lines when it exceeds width."""
    if display_width(token) <= width:
        return [token]
    parts: list[str] = []
    cur = ""
    for ch in token:
        trial = cur + ch
        if cur and display_width(trial) > width:
            parts.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        parts.append(cur)
    return parts or [""]


def wrap_text(text: str, width: int) -> list[str]:
    """Greedy word wrap on terminal cells; never returns an empty list."""
    width = max(4, width)
    lines: list[str] = []
    for para in str(text).splitlines() or [""]:
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = ""
        for word in words:
            for piece in _wrap_long_token(word, width):
                if not cur:
                    cur = piece
                elif display_width(cur) + 1 + display_width(piece) <= width:
                    cur += " " + piece
                else:
                    lines.append(cur)
                    cur = piece
        if cur:
            lines.append(cur)
    return lines or [""]


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def ansi_width(text: str) -> int:
    return display_width(_ANSI_RE.sub("", text))


class UI:
    def __init__(self) -> None:
        self.collector = Collector()
        self.show_landed = show_landed_default()
        self.columns = [c for c in COLUMNS if self.show_landed or c[0] != "landed"]
        self.col_idx = 0
        self.card_idx = 0
        self.scroll: dict[str, int] = {}
        self.flash = ""
        self.flash_at = 0.0
        self.tab_regions: list[tuple[int, int, str]] = []
        self.card_regions: list[tuple[int, int, int, int, Card]] = []
        self.last_seq = -1
        self.last_render = 0.0
        self.width = 120
        self.height = 40
        self.colw = 24
        self.body_h = 32
        self.pending_home = ""
        self.dirty = False
        self.last_frame: list[str] | None = None
        self.last_size: tuple[int, int] = (0, 0)
        self.quitting = False
        self.dialog: DecisionDialog | None = None
        self.dialog_hit: list[tuple[int, int, int, str, int]] = []
        self.dialog_box: tuple[int, int, int, int] | None = None
        self.dialog_scroll = 0
        self._dialog_scroll_follow = False

    # -- helpers ------------------------------------------------------------
    def say(self, msg: str) -> None:
        self.flash = msg
        self.flash_at = time.time()
        self.dirty = True

    def current_key(self) -> str:
        return self.columns[self.col_idx][0] if self.columns else ""

    def toggle_landed(self) -> None:
        self.show_landed = not self.show_landed
        self.columns = [c for c in COLUMNS if self.show_landed or c[0] != "landed"]
        if self.show_landed:
            for i, (key, _) in enumerate(self.columns):
                if key == "landed":
                    self.col_idx = i
                    self.card_idx = 0
                    break
        else:
            self.col_idx = max(0, min(self.col_idx, len(self.columns) - 1))
        set_show_landed_pref(self.show_landed)
        self.dirty = True
        self.say(f"landed column {'shown' if self.show_landed else 'hidden'}")

    def crew_tab_count(self, snap: Snapshot, home_label: str) -> int | None:
        if home_label == ALL_CREW_LABEL:
            count = snap.counts.get(ALL_CREW_LABEL)
            return count if count is not None else None
        count = snap.counts.get(home_label)
        if count is None:
            return None
        if self.show_landed:
            return count
        return max(0, count - snap.landed_counts.get(home_label, 0))

    def current_cards(self) -> list[Card]:
        snap = self.collector.snapshot()
        key = self.current_key()
        return snap.cols.get(key) or []

    def selected(self) -> Card | None:
        cards = self.current_cards()
        if not cards:
            return None
        self.card_idx = min(self.card_idx, len(cards) - 1)
        return cards[self.card_idx]

    # -- focus action -------------------------------------------------------
    def activate(self) -> None:
        """Enter / click on a ticket: a Captain's Call ticket opens its decision
        dialog; every other ticket focuses its agent pane."""
        card = self.selected()
        if card is None:
            self.say("no ticket in this column")
            return
        if card.bucket == "captains_call":
            self.open_decision(card)
            return
        self.open_selected()

    def open_selected(self) -> None:
        card = self.selected()
        if card is None:
            self.say("no ticket in this column")
            return
        snap = self.collector.snapshot()
        tab = card.tab_id
        if not tab and card.pane_id:
            tab = (snap.panes.get(card.pane_id) or {}).get("tab_id", "")
        if tab and self._focus([HERDR_BIN, "tab", "focus", tab]):
            self.say(f"opened {card.task} \u2192 {tab}")
            return
        if card.workspace_id and self._focus([HERDR_BIN, "workspace", "focus", card.workspace_id]):
            self.say(f"opened {card.task} \u2192 workspace {card.workspace_id}")
            return
        wt = card.worktree or "(no worktree yet)"
        self.say(f"{card.task}: no live pane \u00b7 {wt}")

    @staticmethod
    def _focus(cmd: list[str]) -> bool:
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=10, env=_child_env(), stdin=subprocess.DEVNULL)
            return p.returncode == 0
        except Exception:
            return False

    # -- captain's call dialog ---------------------------------------------
    def open_decision(self, card: Card) -> None:
        snap = self.collector.snapshot()
        content = decision_card_content(card, snap.homes, snap.home)
        self.dialog = DecisionDialog(content)
        self.dialog_hit = []
        self.dialog_box = None
        self.dialog_scroll = 0
        self._dialog_scroll_follow = False
        self.say(f"captain's call: {card.task}")
        self.dirty = True

    def close_dialog(self) -> None:
        if self.dialog is not None and not self.dialog.busy:
            self.dialog = None
            self.dirty = True

    def dialog_move(self, delta: int) -> None:
        d = self.dialog
        if d is None or not d.options:
            return
        d.cursor = max(0, min(len(d.options) - 1, d.cursor + delta))
        d.focus = "options"
        self.dirty = True

    def dialog_pick(self, index: int | None = None) -> None:
        """Space / click on an option row: radio-select it, or clear it when it
        is already the selection so a freeform-only answer stays possible."""
        d = self.dialog
        if d is None or not d.options:
            return
        i = d.cursor if index is None else max(0, min(len(d.options) - 1, index))
        d.cursor = i
        d.selected = -1 if d.selected == i else i
        d.focus = "options"
        self.dirty = True

    def dialog_type(self, text: str) -> None:
        d = self.dialog
        if d is None:
            return
        if not d.freeform:
            self.say("this call takes a listed option")
            return
        d.note += text
        d.focus = "note"
        self._dialog_scroll_follow = True
        self.dirty = True

    def dialog_backspace(self) -> None:
        d = self.dialog
        if d is None:
            return
        d.note = d.note[:-1]
        self._dialog_scroll_follow = True
        self.dirty = True

    def dialog_scroll_by(self, delta: int) -> None:
        if self.dialog is None:
            return
        self.dialog_scroll = max(0, self.dialog_scroll + delta)
        self._dialog_scroll_follow = False
        self.dirty = True

    def dialog_toggle_focus(self) -> None:
        d = self.dialog
        if d is None:
            return
        if d.focus == "options":
            d.focus = "note"
            self._dialog_scroll_follow = True
        else:
            d.focus = "options"
        self.dirty = True

    def dialog_submit(self) -> None:
        d = self.dialog
        if d is None or d.busy or d.queued:
            return
        answer = d.display_answer()
        if not answer:
            d.error = "choose an option or type an answer"
            d.busy = False
            self.dirty = True
            return
        if len(answer.encode("utf-8")) > ANSWER_LIMIT:
            d.error = f"answer is too long ({ANSWER_LIMIT} bytes maximum)"
            self.dirty = True
            return
        d.error = ""
        d.busy = True
        d.focus = "options"
        self.dirty = True
        threading.Thread(target=self._submit_worker, args=(d,), daemon=True).start()

    def _submit_worker(self, d: DecisionDialog) -> None:
        ok, detail = run_submit(d)
        if ok:
            d.queued = True
            d.busy = False
            d.done_at = time.time()
            # a wake problem is shown beside the queued state: the answer is
            # already durable, so it must not read as a failed submit
            d.error = detail
            self.say(f"answer queued: {d.key}")
        else:
            d.error = detail or "the answer intake refused this call"
            d.busy = False
        self.dirty = True

    def _click_dialog(self, x: int, y: int, button: int) -> None:
        d = self.dialog
        if d is None:
            return
        if button in (64, 65, 68, 69):
            delta = -WHEEL_ROWS if button in (64, 68) else WHEEL_ROWS
            if self.dialog_scroll > 0 or getattr(self, "_dialog_scroll_max", 0) > 0:
                self.dialog_scroll_by(delta)
            else:
                self.dialog_move(-1 if button in (64, 68) else 1)
            return
        for y1, x1, x2, kind, idx in self.dialog_hit:
            if y1 == y and x1 <= x < x2:
                if kind == "option":
                    self.dialog_pick(idx)
                elif kind == "button":
                    self.dialog_submit()
                elif kind == "note":
                    d.focus = "note"
                    self._dialog_scroll_follow = True
                    self.dirty = True
                return
        box = self.dialog_box
        if box and box[0] <= x < box[2] and box[1] <= y < box[3]:
            return
        self.close_dialog()

    def switch_home(self, label: str) -> None:
        # Optimistic: highlight the tab and show loading state immediately; the
        # collector paints cached cards first and re-runs bearings after.
        self.pending_home = label
        self.collector.set_active(label)
        self.col_idx = 0
        self.card_idx = 0
        self.scroll.clear()
        self.say(f"crew: {label}")

    def scroll_by(self, key: str, delta: int) -> None:
        self.scroll[key] = max(0, self.scroll.get(key, 0) + delta)
        self.dirty = True
        self._anchor_selection(key)

    def scroll_selected(self, delta: int) -> None:
        self.scroll_by(self.current_key(), delta)

    def scroll_bound(self, bottom: bool = False) -> None:
        key = self.current_key()
        cards = self.collector.snapshot().cols.get(key) or []
        self.scroll[key] = len(cards) * 8 if bottom else 0
        self.dirty = True
        self._anchor_selection(key)

    def _anchor_selection(self, key: str) -> None:
        """Keep the selected card inside the visible rows of a column that was
        just scrolled, so render() does not pull the offset back."""
        if key != self.current_key():
            return
        cards = self.collector.snapshot().cols.get(key) or []
        if not cards:
            return
        off = self.scroll.get(key, 0)
        body_h = max(8, self.body_h)
        first = min(len(cards) - 1, off // 8)
        last = min(len(cards) - 1, (off + body_h - 1) // 8)
        self.card_idx = min(max(self.card_idx, first), last)
    def next_home(self, delta: int) -> None:
        snap = self.collector.snapshot()
        if not snap.homes:
            return
        labels = [h.label for h in snap.homes]
        try:
            i = labels.index(self.collector._active)
        except ValueError:
            i = 0
        self.switch_home(labels[(i + delta) % len(labels)])

    # -- mouse --------------------------------------------------------------
    def click(self, x: int, y: int, button: int) -> None:
        _debug(f"click x={x} y={y} button={button} regions={len(self.card_regions)}")
        self.dirty = True
        if self.dialog is not None:
            self._click_dialog(x, y, button)
            return
        if button in (64, 65, 68, 69):  # wheel; shift+wheel = 68/69 (all columns)
            delta = (-WHEEL_ROWS if button in (64, 68) else WHEEL_ROWS)
            if button in (68, 69):
                for key, _ in self.columns:
                    self.scroll_by(key, delta)
            else:
                ci = min(len(self.columns) - 1, max(0, x // max(1, self.colw + 1)))
                self.scroll_by(self.columns[ci][0], delta)
            return
        for x1, x2, label in self.tab_regions:
            if y == 0 and x1 <= x < x2:
                self.switch_home(label)
                return
        for x1, y1, x2, y2, card in self.card_regions:
            if x1 <= x < x2 and y1 <= y < y2:
                _debug(f"hit card {card.task} region=({x1},{y1},{x2},{y2})")
                snap = self.collector.snapshot()
                for ci, (key, _) in enumerate(self.columns):
                    cards = snap.cols.get(key) or []
                    if card in cards:
                        self.col_idx = ci
                        self.card_idx = cards.index(card)
                        break
                self.activate()
                return

    # -- render -------------------------------------------------------------
    def render(self, snap: Snapshot, force: bool = False) -> None:
        try:
            size = shutil.get_terminal_size((120, 40))
            self.width, self.height = size.columns, size.lines
        except Exception:
            pass
        w, h = self.width, self.height
        lines: list[str] = []
        self.tab_regions = []
        self.card_regions = []

        # a crew switch is "done" once the collector publishes that home
        if snap.home is not None and self.pending_home == snap.home.label:
            self.pending_home = ""

        # header / crew tabs
        head = f"{BOLD}{fg(C_TITLE)}Firstmate Flow{RESET} "
        x = len("Firstmate Flow ")
        active_label = self.pending_home or (snap.home.label if snap.home else "")
        for home in snap.homes:
            active = home.label == active_label
            status = snap.activity.get(home.label, "")
            if status in ("working", "blocked"):
                dot_text = "\u25cf "
                dot = f"{fg(C_OK)}\u25cf{RESET} "
            elif status:
                dot_text = "\u25cb "
                dot = f"{fg(C_DIM)}\u25cb{RESET} "
            else:
                dot_text, dot = "", ""
            dot_plain = display_width(dot_text)
            count = self.crew_tab_count(snap, home.label)
            # a known zero is a real answer ("no tickets"), so it shows (0); a
            # home never visited has no count yet and shows its bare name
            text = f"{home.label} ({count})" if count is not None else home.label
            chip = f" {text} "
            head += dot
            if active:
                head += f"{REV}{BOLD}{fg(C_TITLE)}{chip}{RESET}"
            else:
                head += f"{fg(C_DIM)}{chip}{RESET}"
            # the whole chip (plus its trailing space) is clickable
            self.tab_regions.append((x, x + dot_plain + display_width(chip) + 1, home.label))
            head += " "
            x += dot_plain + display_width(chip) + 1
        lines.append(head)

        lines.append(f"{fg(C_BORDER)}{'\u2500' * w}{RESET}")

        # column strip geometry (row-level scrolling: one card = 8 rows)
        gaps = len(self.columns) - 1
        colw = max(10, (w - gaps) // max(1, len(self.columns)))
        self.colw = colw
        body_top = 4          # rows 0-3: header, rule, column titles, rule
        body_bottom = h - 2   # last row is the footer/help line
        body_h = max(8, body_bottom - body_top + 1)
        self.body_h = body_h
        _debug(
            f"render w={w} h={h} body_h={body_h} scroll={dict(self.scroll)} "
            f"cards={ {k: len(v) for k, v in snap.cols.items()} }"
        )

        # clamp every column's row offset, then keep the selection visible
        for ckey, _ in self.columns:
            cards = snap.cols.get(ckey) or []
            max_off = max(0, len(cards) * 8 - body_h)
            self.scroll[ckey] = min(max(0, self.scroll.get(ckey, 0)), max_off)
        key = self.current_key()
        cards_sel = snap.cols.get(key) or []
        if cards_sel:
            self.card_idx = min(self.card_idx, len(cards_sel) - 1)
            off = self.scroll.get(key, 0)
            top = self.card_idx * 8
            bottom = top + 6
            # only move the viewport when the selected card is fully hidden;
            # a partially clipped card is still visible (smooth scrolling)
            if bottom < off:
                off = top
            elif top > off + body_h - 1:
                off = bottom - body_h + 1
            max_off = max(0, len(cards_sel) * 8 - body_h)
            self.scroll[key] = min(max(0, off), max_off)
        else:
            self.card_idx = 0

        # column headers with scroll indicators
        header_cells = []
        for ckey, title in self.columns:
            cards = snap.cols.get(ckey) or []
            total = snap.totals.get(ckey) or len(cards)
            shown = f"{len(cards)}/{total}" if total > len(cards) else str(len(cards))
            label = f"{title} ({shown})"
            header_cells.append(f"{fg(C_ACCENT)}{BOLD}{pad(clip(label, colw), colw)}{RESET}")
        lines.append(f"{fg(C_BORDER)}\u2502{RESET}".join(header_cells))
        lines.append(f"{fg(C_BORDER)}{'\u2500' * w}{RESET}")

        # cards grid: each column is a stack of 8-row card slots, scrolled by rows
        # so a wheel notch moves the content smoothly instead of by whole cards
        grid: list[list[str]] = []
        for ci, (ckey, _) in enumerate(self.columns):
            cards = snap.cols.get(ckey) or []
            off = self.scroll.get(ckey, 0)
            x0 = ci * (colw + 1)
            col_lines = [" " * colw] * body_h
            if not cards:
                placeholder = "  (loading\u2026)" if snap.loading else "  (empty)"
                col_lines[0] = f"{fg(C_DIM)}{pad(placeholder, colw)}{RESET}"
            for i, card in enumerate(cards):
                top = body_top + i * 8 - off
                if top > body_bottom or top + 6 < body_top:
                    continue
                selected = ci == self.col_idx and i == self.card_idx
                card_lines = self.render_card(card, colw, selected, snap)
                for j, line in enumerate(card_lines):
                    y = top + j
                    if body_top <= y <= body_bottom:
                        col_lines[y - body_top] = line
                self.card_regions.append(
                    (x0, max(body_top, top), x0 + colw, min(body_bottom, top + 6) + 1, card)
                )
            grid.append(col_lines)

        for r in range(body_h):
            lines.append(f"{fg(C_BORDER)}\u2502{RESET}".join(col_lines[r] for col_lines in grid))

        # footer
        while len(lines) < h - 1:
            lines.append("")
        help_line = (
            " \u2190\u2192 cols \u00b7 \u2191\u2193 cards \u00b7 pgup/pgdn or wheel scroll \u00b7 "
            "click crew name \u00b7 enter/click decides \u00b7 o opens agent \u00b7 1-9/tab crew (All=fleet) \u00b7 L landed \u00b7 r refresh \u00b7 q quit"
        )
        lines.append(f"{fg(C_DIM)}{clip(help_line, w)}{RESET}")
        if self.flash and time.time() - self.flash_at < 6:
            lines[-1] = f"{fg(C_WARN)}{clip(self.flash, w)}{RESET}"
        elif snap.error:
            lines[-1] = f"{fg(C_BAD)}{clip(snap.error, w)}{RESET}"

        if self.dialog is not None:
            self.render_dialog(lines, w, h)
        self.paint([clip_ansi(line, w) for line in lines[:h]], w, h)

    def paint(self, frame: list[str], w: int, h: int) -> None:
        """Repaint only the lines that changed.

        The board updates one ticket badge at a time without touching the rest
        of the screen, so scrolling and selection stay exactly where they are.
        """
        size = (w, h)
        if self.last_frame is None or self.last_size != size:
            # Absolute cursor positioning instead of newlines: the pane must
            # never scroll, whatever the terminal reports as its height.
            out = ["\x1b[?25l"]
            for i, line in enumerate(frame):
                out.append(f"\x1b[{i + 1};1H")
                out.append(line)
                out.append("\x1b[K")
            out.append("\x1b[J")
            self.last_frame = frame
            self.last_size = size
            sys.stdout.write("".join(out))
            sys.stdout.flush()
            return

        prev = self.last_frame
        out: list[str] = []
        for i, line in enumerate(frame):
            if i < len(prev) and prev[i] == line:
                continue
            out.append(f"\x1b[{i + 1};1H")
            out.append(line)
            out.append("\x1b[K")
        for i in range(len(frame), len(prev)):
            out.append(f"\x1b[{i + 1};1H\x1b[K")
        self.last_frame = frame
        if out:
            sys.stdout.write("\x1b[?25l" + "".join(out))
            sys.stdout.flush()

    def render_dialog(self, lines: list[str], w: int, h: int) -> None:
        """Draw the Captain's Call decision card as a centered modal.

        It mirrors the Lavish board card: the type badge and repo, the title,
        ABOUT / DECIDE context, the authored options with the recommended one
        marked, a note row, and the Queue answer action.
        """
        d = self.dialog
        if d is None:
            return
        self.dialog_hit = []
        self.dialog_box = None
        dw = max(34, min(86, w - 4))
        inner = dw - 4
        cw = inner - 4  # content width inside an option / note card
        rows: list[str] = []
        kinds: list[str] = []

        def add(
            text: str,
            kind: str,
            color: int | None = None,
            bold: bool = False,
        ) -> None:
            body = pad(text, inner)
            styled = body
            if color is not None:
                styled = fg(color) + styled
            if bold:
                styled = BOLD + styled
            if styled != body:
                styled += RESET
            rows.append(styled)
            kinds.append(kind)

        def add_segments(segments: list[tuple[str, str]], kind: str) -> None:
            """One dialog row from (text, style) segments, padded to inner."""
            used = 0
            styled = ""
            for text, style in segments:
                if not text:
                    continue
                used += display_width(text)
                styled += f"{style}{text}{RESET}" if style else text
            rows.append(styled + " " * max(0, inner - used))
            kinds.append(kind)

        def add_kv(key: str, value: str) -> None:
            first = True
            for ln in wrap_text(value, max(10, inner - 9)):
                if first:
                    add_segments([(pad(key, 8), fg(C_DIM)), (ln, "")], "ctx")
                    first = False
                else:
                    add(" " * 9 + ln, "ctx")

        def card_top(border_style: str) -> None:
            rows.append(
                f"{border_style}\u256d" + "\u2500" * (inner - 2) + f"\u256e{RESET}"
            )

        def card_bottom(border_style: str) -> None:
            rows.append(
                f"{border_style}\u2570" + "\u2500" * (inner - 2) + f"\u256f{RESET}"
            )

        def card_row(segments: list[tuple[str, str]], border_style: str) -> None:
            used = 0
            styled = ""
            for text, style in segments:
                if not text:
                    continue
                used += display_width(text)
                styled += f"{style}{text}{RESET}" if style else text
            styled += " " * max(0, cw - used)
            rows.append(f"{border_style}\u2502 {RESET}{styled}{border_style} \u2502{RESET}")

        badge = d.ctype.upper()
        # header: the type badge on the left, the routing repo on the right
        add_segments(
            [
                (badge, BOLD + fg(C_DECIDE if d.ctype == "decision" else C_REVIEW)),
                (" " * max(1, inner - display_width(badge) - display_width(d.repo)), ""),
                (d.repo, fg(C_DIM)),
            ],
            "head",
        )
        for ln in wrap_text(d.title, inner):
            add(ln, "title", bold=True)
        if d.about or d.decide or d.detail:
            add("", "blank")
        if d.about:
            add_kv("ABOUT", d.about)
        if d.decide:
            add_kv("DECIDE", d.decide)
        if d.detail:
            add_kv("DETAIL", d.detail)

        # every option is its own bordered card, the way the board renders it:
        # the selected one keeps the DECISION colour and a filled radio, the
        # keyboard cursor gets a marker and a brightened border, and the
        # recommendation carries the board's REC chip
        for i, opt in enumerate(d.options):
            selected = i == d.selected
            cursor = i == d.cursor and d.focus == "options" and not d.queued
            border = C_DECIDE if selected else (C_ACCENT if cursor else C_BORDER)
            border_style = fg(border) + (BOLD if selected or cursor else "")
            card_top(border_style)
            kinds.append(f"option:{i}")
            lead = "\u25b8 " if cursor else "  "
            mark = "\u25cf " if selected else "\u25cb "
            rec = opt["value"] == d.recommend
            rec_text = " REC " if rec else ""
            room = cw - display_width(lead + mark) - display_width(rec_text)
            label = clip(opt["label"], max(4, room))
            gap = max(0, cw - display_width(lead + mark + label) - display_width(rec_text))
            label_segments = [
                (lead, fg(C_ACCENT) if cursor else ""),
                (mark, fg(C_DECIDE) if selected else fg(C_DIM)),
                (label, BOLD if selected else ""),
                (" " * gap, ""),
            ]
            if rec:
                label_segments.append((rec_text, REV + fg(C_WARN)))
            card_row(label_segments, border_style)
            kinds.append(f"option:{i}")
            if opt["hint"]:
                for ln in wrap_text(opt["hint"], max(8, cw - 2)):
                    card_row([("  " + ln, fg(C_DIM))], border_style)
                    kinds.append(f"option:{i}")
            card_bottom(border_style)
            kinds.append(f"option:{i}")
            # option cards stack tightly, the way the board lists them

        if d.freeform:
            if d.options:
                add("", "blank")
            note_border = C_ACCENT if d.focus == "note" else C_BORDER
            note_style = fg(note_border) + (BOLD if d.focus == "note" else "")
            card_top(note_style)
            kinds.append("note")
            note_text = d.note if d.note else d.freeform_hint
            note_lines = wrap_text(note_text, max(8, cw - 2))
            for li, ln in enumerate(note_lines):
                lead = "\u203a " if li == 0 else "  "
                lead_style = fg(C_ACCENT if d.note else C_DIM) if li == 0 else ""
                body_style = "" if d.note else fg(C_DIM)
                card_row([(lead, lead_style), (ln, body_style)], note_style)
                kinds.append("note")
            card_bottom(note_style)
            kinds.append("note")

        if d.error:
            add("", "blank")
            for ln in wrap_text(d.error, inner):
                add(ln, "error", C_BAD)

        body_rows = list(rows)
        body_kinds = list(kinds)
        footer_rows: list[str] = []
        footer_kinds: list[str] = []

        def add_footer(
            text: str,
            kind: str,
            color: int | None = None,
            bold: bool = False,
        ) -> None:
            body = pad(text, inner)
            styled = body
            if color is not None:
                styled = fg(color) + styled
            if bold:
                styled = BOLD + styled
            if styled != body:
                styled += RESET
            footer_rows.append(styled)
            footer_kinds.append(kind)

        def add_footer_segments(segments: list[tuple[str, str]], kind: str) -> None:
            used = 0
            styled = ""
            for text, style in segments:
                if not text:
                    continue
                used += display_width(text)
                styled += f"{style}{text}{RESET}" if style else text
            footer_rows.append(styled + " " * max(0, inner - used))
            footer_kinds.append(kind)

        add_footer("", "blank")
        status = "queueing\u2026" if d.busy else ("\u2713 queued" if d.queued else "")
        button_segments = [
            (" Queue answer ", REV + BOLD + fg(C_OK if d.queued else C_ACCENT))
        ]
        if status:
            button_segments.append(("  ", ""))
            button_segments.append(
                (f" {status} ", REV + fg(C_OK) if d.queued else fg(C_WARN))
            )
        add_footer_segments(button_segments, "button")
        help_bits = [
            "esc close",
            "\u2191\u2193 move",
            "space pick",
            "enter queue",
        ]
        # Keep Queue answer + help visible; scroll only the card body above them.
        # The whole modal must fit in the terminal (borders add two rows).
        max_inner_rows = max(6, h - 2)
        budget = max(4, max_inner_rows - len(footer_rows) - 1)
        scroll_max = max(0, len(body_rows) - budget)
        self._dialog_scroll_max = scroll_max
        if scroll_max:
            help_bits.insert(1, "pgup/pgdn or wheel scroll")
        if self._dialog_scroll_follow and d.focus == "note":
            self.dialog_scroll = scroll_max
            self._dialog_scroll_follow = False
        self.dialog_scroll = min(max(0, self.dialog_scroll), scroll_max)
        if scroll_max:
            top = self.dialog_scroll + 1
            bottom = min(len(body_rows), self.dialog_scroll + budget)
            help_bits.insert(0, f"lines {top}-{bottom} of {len(body_rows)}")
        add_footer(" \u00b7 ".join(help_bits), "help", C_DIM)

        if scroll_max:
            body_rows = body_rows[self.dialog_scroll : self.dialog_scroll + budget]
            body_kinds = body_kinds[self.dialog_scroll : self.dialog_scroll + budget]
        rows = body_rows + footer_rows
        kinds = body_kinds + footer_kinds

        box_h = len(rows) + 2
        y0 = max(0, min(max(0, h - box_h - 1), (h - box_h) // 2))
        x0 = max(0, (w - dw) // 2)
        border = fg(C_DECIDE if d.ctype == "decision" else C_BORDER)
        # the type badge lives once, on the card's own header row inside
        top = "\u256d" + "\u2500" * (dw - 2) + "\u256e"
        box = [f"{border}{top}{RESET}"]
        for r in rows:
            box.append(f"{border}\u2502{RESET} " + r + f" {border}\u2502{RESET}")
        box.append(f"{border}\u2570" + "\u2500" * (dw - 2) + f"\u256f{RESET}")

        self.dialog_box = (x0, y0, x0 + dw, y0 + box_h)
        left = " " * x0
        for i, dl in enumerate(box):
            y = y0 + i
            if 0 <= y < len(lines):
                lines[y] = left + dl + " " * max(0, w - x0 - ansi_width(dl))
        for i, kind in enumerate(kinds):
            y = y0 + 1 + i
            if kind.startswith("option:"):
                try:
                    idx = int(kind.split(":", 1)[1])
                except (ValueError, IndexError):
                    continue
                self.dialog_hit.append((y, x0, x0 + dw, "option", idx))
            elif kind == "button":
                self.dialog_hit.append((y, x0, x0 + dw, "button", 0))
            elif kind == "note":
                self.dialog_hit.append((y, x0, x0 + dw, "note", 0))

    def _crew_label(self, snap: Snapshot, home_path: str) -> str:
        if not home_path:
            return ""
        target = os.path.realpath(home_path)
        for home in snap.homes:
            if home.path and os.path.realpath(home.path) == target:
                return home.label
        return os.path.basename(home_path) or home_path

    def render_card(self, card: Card, colw: int, selected: bool, snap: Snapshot | None = None) -> list[str]:
        border_c = C_TITLE if selected else C_BORDER
        cardw = max(10, colw - 1)  # one column of breathing room between cards
        inner = max(4, cardw - 4)
        # leave a cell for the space before the dashes, so the corner always
        # lands on the same column as the card's right border
        top_lbl = clip(card.id, max(1, inner - 1))
        # ╭─ id ────╮ : 3 prefix + label + space + dashes + corner must equal cardw
        top = (
            f"\u256d\u2500 {top_lbl} "
            + "\u2500" * max(0, inner - display_width(top_lbl) - 1)
            + "\u256e"
        )
        top += " " * max(0, cardw - display_width(top))

        def mid(plain: str, color: int | None = None) -> str:
            body = pad(plain, inner)
            if color is not None and body.strip():
                body = f"{fg(color)}{body}{RESET}"
            return f"\u2502 {body} \u2502"

        lines = [
            f"{fg(border_c)}{top}{RESET}",
            mid(card.badge, card.badge_color),
            mid(self.card_agent_line(card), C_DIM),
            mid(card.title or card.task),
            mid(card.doing, C_WARN),
            mid(
                self.card_footer_line(
                    card,
                    self._crew_label(snap, card.home_path)
                    if snap is not None and snap.home is not None and is_aggregate_home(snap.home)
                    else "",
                ),
                C_DIM,
            ),
            f"{fg(border_c)}{'\u2570' + '\u2500' * (cardw - 2) + '\u256f'}{RESET}",
        ]
        # pad each row to the full column width so the separator keeps a gap
        return [line + " " * max(0, colw - cardw) for line in lines]

    @staticmethod
    def card_agent_line(card: Card) -> str:
        parts = [p for p in (card.agent, card.model, card.effort) if p]
        return "\u00b7".join(parts) if parts else "no agent yet"

    @staticmethod
    def card_footer_line(card: Card, crew: str = "") -> str:
        crew_bit = f"\u2316 {crew} \u00b7 " if crew else ""
        if card.bucket == "landed" and card.artifact:
            art = card.artifact.rstrip("/").split("/")[-1]
            return f"{crew_bit}\u2197 {art}"
        if card.worktree:
            parts = card.worktree.rstrip("/").split("/")
            short = "/".join(parts[-2:])
            return f"{crew_bit}\u2338 {short}"
        if card.bucket == "charted":
            return f"{crew_bit}no worktree yet" if crew else "no worktree yet"
        if crew:
            return crew_bit.rstrip(" \u00b7 ")
        return card.mode or card.kind or ""


def clip_ansi(line: str, width: int) -> str:
    """Clip a line to width terminal cells, keeping ANSI sequences intact."""
    out = []
    used = 0
    i = 0
    while i < len(line) and used < width:
        ch = line[i]
        if ch == "\x1b":
            m = re.match(r"\x1b\[[0-9;?]*[a-zA-Z]", line[i:])
            if m:
                out.append(m.group(0))
                i += len(m.group(0))
                continue
        w = display_width(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
        i += 1
    return "".join(out) + RESET


# ----------------------------------------------------------------------------
# input
# ----------------------------------------------------------------------------


def parse_input(buf: bytes, ui: UI) -> bytes:
    """Parse a chunk of stdin bytes; returns unconsumed remainder."""
    while buf:
        if buf.startswith(b"\x1b[<"):
            m = re.match(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", buf)
            if not m:
                if len(buf) < 24 and b"M" not in buf and b"m" not in buf:
                    return buf
                buf = buf[1:]
                continue
            but, x, y, kind = _int(m.group(1)), _int(m.group(2)), _int(m.group(3)), m.group(4)
            buf = buf[m.end() :]
            if kind == b"M":
                ui.click(x - 1, y - 1, but)
                ui.dirty = True
            continue
        if buf.startswith(b"\x1b["):
            km = re.match(rb"\x1b\[(\d+);(\d+)u", buf)
            if km:
                cp, mod = _int(km.group(1)), _int(km.group(2))
                buf = buf[km.end() :]
                ui.dirty = True
                # Kitty keyboard protocol: shift+L toggles Landed (lowercase l is column right)
                if cp in (76, 108) and (mod & 1):
                    ui.toggle_landed()
                continue
            m = re.match(rb"\x1b\[([0-9]*)([ABCDZHF~])", buf)
            if not m:
                if len(buf) < 8:
                    return buf
                buf = buf[1:]
                continue
            num, code = m.group(1), m.group(2)
            buf = buf[m.end() :]
            ui.dirty = True
            if ui.dialog is not None:
                if code == b"A":
                    ui.dialog_move(-1)
                elif code == b"B":
                    ui.dialog_move(1)
                elif code == b"Z":
                    ui.dialog_toggle_focus()
                elif code == b"~" and num == b"5":
                    ui.dialog_scroll_by(-max(1, ui.body_h // 3))
                elif code == b"~" and num == b"6":
                    ui.dialog_scroll_by(max(1, ui.body_h // 3))
                continue
            if code == b"A":
                ui.card_idx = max(0, ui.card_idx - 1)
            elif code == b"B":
                ui.card_idx += 1
            elif code == b"C":
                ui.col_idx = min(len(ui.columns) - 1, ui.col_idx + 1)
                ui.card_idx = 0
            elif code == b"D":
                ui.col_idx = max(0, ui.col_idx - 1)
                ui.card_idx = 0
            elif code == b"Z":
                ui.next_home(-1)
            elif code == b"~" and num == b"5":
                ui.scroll_selected(-ui.body_h)
            elif code == b"~" and num == b"6":
                ui.scroll_selected(ui.body_h)
            elif code == b"H" or (code == b"~" and num == b"1"):
                ui.scroll_bound(bottom=False)
            elif code == b"F" or (code == b"~" and num == b"4"):
                ui.scroll_bound(bottom=True)
            continue
        if buf[0] == 0x1b:
            buf = buf[1:]
            if ui.dialog is not None:
                ui.close_dialog()
            continue
        # decode one whole character, waiting for the rest of a multibyte
        # sequence instead of dropping it (captain notes are typed here)
        lead = buf[0]
        size = 1
        if lead & 0xE0 == 0xC0:
            size = 2
        elif lead & 0xF0 == 0xE0:
            size = 3
        elif lead & 0xF8 == 0xF0:
            size = 4
        if len(buf) < size:
            return buf
        chunk = buf[:size]
        buf = buf[size:]
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue
        ui.dirty = True
        if text == "\x0c":  # ctrl+L toggles Landed when the terminal sends it
            ui.toggle_landed()
            continue
        if ui.dialog is not None:
            # the modal owns the keyboard: Esc closes, arrows/space choose,
            # every printable key edits the note
            if text in ("\x03", "\x04"):
                ui.quitting = True
            elif text in ("\r", "\n"):
                ui.dialog_submit()
            elif text in ("\x7f", "\b"):
                ui.dialog_backspace()
            elif text == "\x15":  # ctrl+u clears the note
                ui.dialog.note = ""
                ui._dialog_scroll_follow = True
                ui.dirty = True
            elif text == "\t":
                ui.dialog_toggle_focus()
            elif text == " ":
                if ui.dialog.focus == "note":
                    ui.dialog_type(" ")
                else:
                    ui.dialog_pick()
            elif text.isprintable():
                ui.dialog_type(text)
            continue
        if text in ("q", "\x03", "\x04"):
            ui.quitting = True
        elif text == "r":
            ui.collector.refresh_now()
            ui.say("refreshing\u2026")
        elif text == "\t":
            ui.next_home(1)
        elif text in "123456789":
            snap = ui.collector.snapshot()
            i = _int(text) - 1
            if i < len(snap.homes):
                ui.switch_home(snap.homes[i].label)
        elif text in ("[",):
            ui.next_home(-1)
        elif text in ("]",):
            ui.next_home(1)
        elif text in ("g",):
            ui.scroll_bound(bottom=False)
        elif text in ("G",):
            ui.scroll_bound(bottom=True)
        elif text in ("j",):
            ui.card_idx += 1
        elif text in ("k",):
            ui.card_idx = max(0, ui.card_idx - 1)
        elif text in ("l",):
            ui.col_idx = min(len(ui.columns) - 1, ui.col_idx + 1)
            ui.card_idx = 0
        elif text in ("h",):
            ui.col_idx = max(0, ui.col_idx - 1)
            ui.card_idx = 0
        elif text == "L":
            ui.toggle_landed()
        elif text == "o":
            ui.open_selected()
        elif text in ("\r", "\n"):
            ui.activate()
    return buf


# ----------------------------------------------------------------------------
# non-interactive mode
# ----------------------------------------------------------------------------


def once(active_label: str = "", all_homes: bool = False) -> int:
    homes = discover_homes()
    if not homes:
        print("no Firstmate homes found (set FM_FLOW_HOMES or homes.conf)", file=sys.stderr)
        return 1
    agents, panes = herdr_agents()
    meta_index = build_meta_index(homes)
    selected = [h for h in homes if all_homes or not active_label or h.label == active_label] or homes
    for home in selected:
        snap = bearings_snapshot(home)
        cols: dict[str, list[Card]] = {k: [] for k, _ in COLUMNS}
        if snap:
            cols, _totals = make_cards(home, snap, meta_index, agents, panes, homes)
        print(f"== {home.label}  ({home.path})")
        for key, title in COLUMNS:
            cards = cols.get(key) or []
            print(f"  {title} ({len(cards)})")
            for c in cards:
                agent = " ".join(p for p in (c.agent, c.model, c.effort) if p) or "-"
                pane = c.pane_id or "-"
                line = f"    {c.id} [{c.badge}] {agent} pane={pane}"
                if c.bucket == "charted":
                    line += f" wt={c.worktree or '-'}"
                print(line)
        print()
    return 0


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if "--homes" in argv:
        discover_homes(verbose=True)
        return 0
    if "--once" in argv:
        active = ""
        if "--home" in argv:
            active = argv[argv.index("--home") + 1]
        return once(active_label=active, all_homes="--all" in argv)

    if not os.isatty(0) or not os.isatty(1):
        return once()

    ui = UI()
    ui.collector.start()

    fd = sys.stdin.fileno()
    old = None
    try:
        import termios
        import tty

        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stdout.write("\x1b[?1049h\x1b[?1000h\x1b[?1006h\x1b[?25l")
        sys.stdout.flush()

        pending = b""
        while not ui.quitting:
            snap = ui.collector.snapshot()
            now = time.time()
            if (
                ui.dialog is not None
                and ui.dialog.queued
                and now - ui.dialog.done_at > 1.6
            ):
                # the answer landed: retire the modal and re-read the fleet so
                # the answered ticket leaves Captain's Call
                ui.dialog = None
                ui.collector.refresh_now()
                ui.dirty = True
            # repaint immediately on input; the diff painter makes this cheap
            if ui.dirty or snap.seq != ui.last_seq or now - ui.last_render > 1.0:
                ui.last_seq = snap.seq
                ui.last_render = now
                ui.dirty = False
                ui.render(snap)
            r, _, _ = select.select([fd], [], [], 0.25)
            if r:
                data = os.read(fd, 4096)
                if not data:
                    break
                pending = parse_input(pending + data, ui)
                # coalesce a burst of wheel/key events into a single repaint
                for _ in range(16):
                    r2, _, _ = select.select([fd], [], [], 0.0)
                    if not r2:
                        break
                    more = os.read(fd, 4096)
                    if not more:
                        break
                    pending = parse_input(pending + more, ui)
    finally:
        try:
            sys.stdout.write("\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        except Exception:
            pass
        if old is not None:
            import termios

            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        ui.collector.stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)
