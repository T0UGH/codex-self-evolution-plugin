#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "[warn] install-codex-hook.sh is deprecated; using scripts/install.sh" >&2
exec "$REPO/scripts/install.sh" "$@"
