#!/usr/bin/env bash
# Firstmate Flow board launcher: multi-home read-only kanban TUI for Herdr.
#
#   scripts/kanban-view.sh            -> interactive TUI
#   scripts/kanban-view.sh --once     -> one plain-text frame (probe/CI)
#   scripts/kanban-view.sh --homes    -> list discovered Firstmate homes
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional debug log: a `debug_log` file in the plugin config dir either contains
# a log path or (when empty) enables <config>/debug_log.log.
cfg_dir="${HERDR_PLUGIN_CONFIG_DIR:-$HOME/.config/herdr/plugins/config/herdr-firstmate-flow}"
if [ -e "$cfg_dir/debug_log" ]; then
  debug_path="$(head -n1 "$cfg_dir/debug_log" 2>/dev/null || true)"
  [ -n "$debug_path" ] || debug_path="$cfg_dir/debug_log.log"
  export FM_FLOW_DEBUG="$debug_path"
fi

exec python3 "$SCRIPT_DIR/flow_tui.py" "$@"
