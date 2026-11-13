#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  PYTHON=""
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(not ((3,10) <= sys.version_info[:2] <= (3,12)))' >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
  if [ -z "$PYTHON" ]; then
    printf '%s\n' 'Python 3.10–3.12 не найден. Установите Python и модуль venv.' >&2
    exit 1
  fi
  "$PYTHON" -m venv .venv
fi
exec .venv/bin/python launcher.py
