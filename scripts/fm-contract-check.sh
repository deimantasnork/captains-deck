#!/usr/bin/env bash
# Captain's Deck <-> Firstmate contract check.
#
# Thin wrapper so the check reads naturally after an `updatefirstmate`:
#
#   scripts/fm-contract-check.sh --home ~/firstmate
#   scripts/fm-contract-check.sh --home /tmp/firstmate --json
#
# See scripts/fm_contract_check.py for the surfaces it asserts.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/fm_contract_check.py" "$@"
