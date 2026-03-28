#!/bin/sh
# Use the project's interpreter, regardless of the caller's working directory.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
    echo 'Missing .venv. Follow the first-time setup in README.md.' >&2
    exit 1
fi
exec .venv/bin/python scripts/start_local.py "$@"
