#!/usr/bin/env bash
# Idempotent launcher for the Flow overlay (open / focus / toggle close).
set -uo pipefail

herdr_bin="${HERDR_BIN_PATH:-herdr}"

open_pane() {
  exec "$herdr_bin" plugin pane open \
    --plugin herdr-firstmate-flow \
    --entrypoint flow \
    --placement overlay \
    --focus
}

decision="OPEN"
if command -v python3 >/dev/null 2>&1; then
  panes="$("$herdr_bin" pane list 2>/dev/null || true)"
  if [ -n "$panes" ]; then
    decision="$(printf '%s' "$panes" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("OPEN"); sys.exit(0)
res = data.get("result", data)
panes = res.get("panes", []) if isinstance(res, dict) else []
flow = None
for p in panes:
    name = (p.get("label") or p.get("title") or "")
    if name == "Flow":
        flow = p
        break
if not flow:
    print("OPEN"); sys.exit(0)
pid = flow.get("pane_id") or ""
if not pid:
    print("OPEN"); sys.exit(0)
if flow.get("focused"):
    print("CLOSE " + str(pid))
else:
    print("FOCUS " + str(pid))
' 2>/dev/null || echo OPEN)"
  fi
fi

case "$decision" in
  "FOCUS "*)
    pid="${decision#FOCUS }"
    exec "$herdr_bin" plugin pane focus "$pid"
    ;;
  "CLOSE "*)
    pid="${decision#CLOSE }"
    exec "$herdr_bin" pane close "$pid"
    ;;
  *)
    open_pane
    ;;
esac
