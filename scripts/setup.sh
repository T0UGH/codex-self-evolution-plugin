#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    printf '[fail] uv not found on PATH. Install uv first: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
    exit 1
fi

exec uvx csep setup "$@"
