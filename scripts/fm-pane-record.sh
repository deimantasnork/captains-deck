#!/usr/bin/env bash
# Shared pane bookkeeping for Captain's Deck launchers.
#
# The deck only focuses or closes a pane it can prove is its own: the pane id
# is in this plugin's record file, or the pane's foreground process is the
# board itself (how panes opened before records existed are adopted). A pane
# that merely shares the "Flow" label is never touched.
#
# Sourced by open-flow.sh and open-captain-deck.sh.

fm_config_dir() {
  if [ -n "${HERDR_PLUGIN_CONFIG_DIR:-}" ]; then
    printf '%s' "$HERDR_PLUGIN_CONFIG_DIR"
  else
    printf '%s/plugins/config/herdr-firstmate-flow' "${HERDR_CONFIG_DIR:-$HOME/.config/herdr}"
  fi
}

fm_record_file() {  # <name> -> path
  printf '%s/%s' "$(fm_config_dir)" "$1"
}

fm_pane_id_ok() {  # <pane-id>
  case "${1:-}" in
    "" | *[!A-Za-z0-9:._-]*) return 1 ;;
  esac
  [ "${#1}" -le 128 ]
}

fm_pane_recorded() {  # <file> <pane-id>
  fm_pane_id_ok "${2:-}" || return 1
  [ -f "$1" ] || return 1
  grep -qxF -- "$2" "$1" 2>/dev/null
}

fm_pane_record() {  # <file> <pane-id>
  fm_pane_id_ok "${2:-}" || return 0
  fm_pane_recorded "$1" "$2" && return 0
  mkdir -p -- "$(dirname -- "$1")" 2>/dev/null || return 0
  printf '%s\n' "$2" >>"$1"
}

fm_pane_forget() {  # <file> <pane-id>
  [ -f "$1" ] || return 0
  fm_pane_id_ok "${2:-}" || return 0
  local tmp
  tmp="$(mktemp "${1}.XXXXXX")" || return 0
  grep -vxF -- "$2" "$1" >"$tmp" 2>/dev/null || true
  mv -f -- "$tmp" "$1" 2>/dev/null || { rm -f -- "$tmp"; return 0; }
}

fm_pane_ids() {  # stdin: pane list JSON -> pane ids, one per line
  python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
res = data.get("result", data)
for p in res.get("panes", []) if isinstance(res, dict) else []:
    pid = p.get("pane_id") or ""
    if pid:
        print(pid)
'
}

# fm_pane_is_board <herdr-bin> <pane-id>
# 0 = the foreground process is this board, 1 = it is something else,
# 2 = the probe could not answer. Callers never destroy on an unknown.
fm_pane_is_board() {
  "$1" pane process-info --pane "$2" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(2)
res = data.get("result", data)
info = res.get("process_info", {}) if isinstance(res, dict) else {}
procs = info.get("foreground_processes")
if not isinstance(procs, list):
    sys.exit(2)
for p in procs:
    argv = " ".join(p.get("argv") or []) if isinstance(p, dict) else ""
    if "flow_tui.py" in argv or "kanban-view.sh" in argv:
        sys.exit(0)
sys.exit(1)
' 2>/dev/null
}
