#!/usr/bin/env bash
# Contract-test stub: emit the recorded bearings snapshot regardless of flags.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec cat "$here/../../bearings_snapshot.json"
