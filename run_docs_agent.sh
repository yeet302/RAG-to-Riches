#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"
if [ ! -x .venv/bin/python ]; then
    echo 'Run: python3 -m venv .venv' >&2
    echo 'Then: .venv/bin/python -m pip install -r docs_agent/requirements.txt' >&2
    exit 1
fi
exec .venv/bin/python -m docs_agent.cli "$@"
