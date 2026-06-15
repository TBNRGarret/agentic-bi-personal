#!/usr/bin/env bash
# Strict Sprint 1 acceptance verification wrapper.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -x .venv/bin/python ]]; then
  PYTHON_CMD=.venv/bin/python
else
  PYTHON_CMD=python
fi
$PYTHON_CMD scripts/verify_system.py "$@"
