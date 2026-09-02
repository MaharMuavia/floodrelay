#!/usr/bin/env bash
# End-to-end check. Asserts decisions are raised and that nothing is dispatched
# without an approved decision card.
set -euo pipefail
cd "$(dirname "$0")/../backend"
PYTHONIOENCODING=utf-8 uv run python ../scripts/e2e.py "$@"
