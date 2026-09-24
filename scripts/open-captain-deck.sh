#!/usr/bin/env bash
# Open the read-only Flow board full-pane in a dedicated "captain's deck" workspace.
set -euo pipefail

herdr_bin="${HERDR_BIN_PATH:-herdr}"
label="captain's deck"

ws_id="$("$herdr_bin" workspace list 2>/dev/null | python3 -c '
import json, sys
label = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
res = data.get("result", data)
workspaces = res.get("workspaces", []) if isinstance(res, dict) else []
for w in workspaces:
    if (w.get("label") or "") == label:
        print(w.get("workspace_id") or "")
        sys.exit(0)
sys.exit(1)
' "$label" 2>/dev/null || true)"

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

# Focus an existing Flow pane in this workspace instead of stacking duplicates.
# The pane's tab is brought forward first: focusing a pane that lives in another
# tab does not switch the visible tab, so the action would look like it did
# nothing while the board stayed hidden behind the shell.
flow_row="$("$herdr_bin" pane list --workspace "$ws_id" 2>/dev/null | python3 -c '
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
' 2>/dev/null || true)"
flow_pane="$(printf '%s' "$flow_row" | cut -f1)"
flow_tab="$(printf '%s' "$flow_row" | cut -f2)"

if [ -n "$flow_tab" ]; then
  "$herdr_bin" tab focus "$flow_tab" >/dev/null 2>&1 || true
fi
if [ -n "$flow_pane" ]; then
  exec "$herdr_bin" plugin pane focus "$flow_pane"
fi

exec "$herdr_bin" plugin pane open \
  --plugin herdr-firstmate-flow \
  --entrypoint flow \
  --placement tab \
  --workspace "$ws_id" \
  --focus
