#!/usr/bin/env bash
# Open the read-only Flow board full-pane in a dedicated "captain's deck" workspace.
set -euo pipefail

herdr_bin="${HERDR_BIN_PATH:-herdr}"
label="captain's deck"

# `alive` when the pane's foreground process is the board TUI, `stale` when the
# pane still carries the Flow label but its process exited (pressing `q` quits
# the board and leaves the pane at a shell prompt).
board_state() {
  "$herdr_bin" pane process-info --pane "$1" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("unknown")
    sys.exit(0)
res = data.get("result", data)
info = res.get("process_info", {}) if isinstance(res, dict) else {}
procs = info.get("foreground_processes")
if not isinstance(procs, list):
    print("unknown")
    sys.exit(0)
for p in procs:
    argv = " ".join(p.get("argv") or []) if isinstance(p, dict) else ""
    if "flow_tui.py" in argv or "kanban-view.sh" in argv:
        print("alive")
        sys.exit(0)
print("stale")
' 2>/dev/null || echo unknown
}

flow_pane_of() {
  "$herdr_bin" pane list --workspace "$1" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
res = data.get("result", data)
for p in res.get("panes", []) if isinstance(res, dict) else []:
    if (p.get("label") or "") == "Flow":
        print("{}\t{}".format(p.get("pane_id") or "", p.get("tab_id") or ""))
        break
' 2>/dev/null || true
}

# Deck workspaces match the label case- and space-insensitively, so a
# hand-renamed "Captain's Deck" is reused instead of duplicated.
deck_ws="$("$herdr_bin" workspace list 2>/dev/null | python3 -c '
import json, sys
target = " ".join(sys.argv[1].split()).lower()
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
res = data.get("result", data)
for w in res.get("workspaces", []) if isinstance(res, dict) else []:
    label = " ".join((w.get("label") or "").split()).lower()
    if label == target and w.get("workspace_id"):
        print(w["workspace_id"])
' "$label" 2>/dev/null || true)"

# Prefer a deck whose board is already running. Remember the first deck and the
# first stale board so they can be repaired instead of duplicated.
ws_id=""
flow_pane=""
flow_tab=""
stale_pane=""
stale_ws=""
first_ws=""
while IFS= read -r candidate; do
  [ -n "$candidate" ] || continue
  [ -n "$first_ws" ] || first_ws="$candidate"
  row="$(flow_pane_of "$candidate")"
  pane="$(printf '%s' "$row" | cut -f1)"
  tab="$(printf '%s' "$row" | cut -f2)"
  [ -n "$pane" ] || continue
  state="$(board_state "$pane")"
  if [ "$state" != "stale" ]; then
    # alive (or unprobeable, so never destroyed) wins
    ws_id="$candidate"
    flow_pane="$pane"
    flow_tab="$tab"
    break
  fi
  if [ -z "$stale_pane" ]; then
    stale_pane="$pane"
    stale_ws="$candidate"
  fi
done <<< "$deck_ws"

# A running board wins; otherwise repair the stale deck; otherwise reuse the
# first deck workspace; otherwise make one.
if [ -z "$ws_id" ] && [ -n "$stale_ws" ]; then
  ws_id="$stale_ws"
fi
if [ -z "$ws_id" ]; then
  ws_id="$first_ws"
fi
if [ -z "$ws_id" ]; then
  created="$("$herdr_bin" workspace create --label "$label" --no-focus)"
  ws_id="$(printf '%s' "$created" | python3 -c '
import json, sys
data = json.load(sys.stdin)
res = data.get("result", data)
w = res.get("workspace", {}) if isinstance(res, dict) else {}
print(w.get("workspace_id") or "")
')"
fi
if [ -z "$ws_id" ]; then
  echo "Could not resolve workspace id for $label" >&2
  exit 1
fi

# Focus a live board. The pane's tab is brought forward first: focusing a pane
# that lives in another tab does not switch the visible tab, so the action
# would look like it did nothing while the board stayed hidden behind the shell.
if [ -n "$flow_pane" ]; then
  if [ -n "$flow_tab" ]; then
    "$herdr_bin" tab focus "$flow_tab" >/dev/null 2>&1 || true
  fi
  exec "$herdr_bin" plugin pane focus "$flow_pane"
fi

# No live board: open a fresh one first, then retire a stale "Flow" pane, so
# the workspace is never left empty and the fresh board cannot fail silently.
"$herdr_bin" plugin pane open \
  --plugin herdr-firstmate-flow \
  --entrypoint flow \
  --placement tab \
  --workspace "$ws_id" \
  --focus
if [ -n "$stale_pane" ]; then
  "$herdr_bin" pane close "$stale_pane" >/dev/null 2>&1 || true
fi
