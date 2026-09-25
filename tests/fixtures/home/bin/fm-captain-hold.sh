#!/usr/bin/env bash
# Contract-test stub: the fixture only needs the intake to exist and run.
set -euo pipefail
case "${1:-}" in
  binding) echo "captured-source=herdr-firstmate-flow" ;;
  *) echo "stub ok" ;;
esac
