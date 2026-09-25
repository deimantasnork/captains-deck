#!/usr/bin/env bash
# Idempotent launcher for the Flow overlay (open / focus / toggle close).
#
# Only a pane this plugin can prove is its own is focused or closed: its id is
# in the overlay record, or its foreground process is the board itself. A user
# pane that merely shares the "Flow" label is left alone.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fm-pane-record.sh
. "$SCRIPT_DIR/fm-pane-record.sh"

herdr_bin="${HERDR_BIN_PATH:-herdr}"
record="$(fm_record_file flow-overlay.panes)"

panes="$("$herdr_bin" pane list 2>/dev/null || true)"

target=""
if [ -n "$panes" ]; then
  target="$(printf '%s' "$panes" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
res = data.get("result", data)
for p in res.get("panes", []) if isinstance(res, dict) else []:
    name = p.get("label") or p.get("title") or ""
    if name == "Flow" and p.get("pane_id"):
        print("{}\t{}".format(p["pane_id"], "1" if p.get("focused") else "0"))
        break
' 2>/dev/null || true)"
fi

if [ -n "$target" ]; then
  pid="${target%%$'\t'*}"
  focused="${target#*$'\t'}"
  mine=0
  if fm_pane_recorded "$record" "$pid"; then
    mine=1
  elif fm_pane_is_board "$herdr_bin" "$pid"; then
    mine=1
    fm_pane_record "$record" "$pid"
  fi
  if [ "$mine" = "1" ]; then
    if [ "$focused" = "1" ]; then
      "$herdr_bin" pane close "$pid" >/dev/null 2>&1 || true
      fm_pane_forget "$record" "$pid"
    else
      exec "$herdr_bin" plugin pane focus "$pid"
    fi
    exit 0
  fi
fi

# No overlay of ours: open one and record the pane it created. The record is
# bookkeeping only - the process probe still decides whether a pane is the
# board, so an unrecorded open is safe, just not toggle-closeable yet.
before="$(printf '%s' "$panes" | fm_pane_ids | sort)"
"$herdr_bin" plugin pane open \
  --plugin herdr-firstmate-flow \
  --entrypoint flow \
  --placement overlay \
  --focus
after="$("$herdr_bin" pane list 2>/dev/null | fm_pane_ids | sort)"
new_pane="$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | grep -m1 . || true)"
if [ -n "$new_pane" ]; then
  fm_pane_record "$record" "$new_pane"
fi
exit 0
