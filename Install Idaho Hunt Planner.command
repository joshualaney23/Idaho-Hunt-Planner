#!/usr/bin/env bash
# Double-click me in Finder (T-30): runs scripts/setup.sh from this repo in Terminal.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
./scripts/setup.sh
printf '\nDone. You can close this window.\n'
