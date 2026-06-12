#!/usr/bin/env python3
"""Synchronize generated plugin bundle mirrors from the package bundle."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROOT = ROOT / "src" / "codex_self_evolution" / "plugin_bundle"
MIRROR_PAIRS = (
    (
        CANONICAL_ROOT / ".codex-plugin" / "plugin.json",
        ROOT / "plugins" / "codex-self-evolution" / ".codex-plugin" / "plugin.json",
    ),
    (
        CANONICAL_ROOT / ".codex-plugin" / "hooks.json",
        ROOT / "plugins" / "codex-self-evolution" / ".codex-plugin" / "hooks.json",
    ),
    (
        CANONICAL_ROOT / "skills" / "csep-session-recall" / "SKILL.md",
        ROOT / "plugins" / "codex-self-evolution" / "skills" / "csep-session-recall" / "SKILL.md",
    ),
    (
        CANONICAL_ROOT / "skills" / "csep-session-recall" / "SKILL.md",
        ROOT / "skills" / "csep-session-recall" / "SKILL.md",
    ),
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line flags for mirror sync or drift check mode."""
    parser = argparse.ArgumentParser(
        description="Sync plugin bundle mirrors from src/codex_self_evolution/plugin_bundle.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if any generated mirror differs from the canonical bundle",
    )
    return parser.parse_args(argv)


def rel(path: Path) -> str:
    """Return a stable repository-relative path for human-readable output."""
    return str(path.relative_to(ROOT))


def differs(source: Path, target: Path) -> bool:
    """Report whether target is missing or its bytes differ from source."""
    if not target.exists():
        return True
    return source.read_bytes() != target.read_bytes()


def check_mirrors() -> list[str]:
    """Return all generated mirror paths that drift from the canonical bundle."""
    drifted: list[str] = []
    for source, target in MIRROR_PAIRS:
        if not source.exists():
            raise FileNotFoundError(f"canonical source missing: {rel(source)}")
        if differs(source, target):
            drifted.append(f"{rel(target)} differs from {rel(source)}")
    return drifted


def sync_mirrors() -> None:
    """Copy canonical plugin artifacts into generated mirror locations."""
    for source, target in MIRROR_PAIRS:
        if not source.exists():
            raise FileNotFoundError(f"canonical source missing: {rel(source)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def main(argv: list[str] | None = None) -> int:
    """Run the plugin bundle mirror sync command."""
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.check:
        drifted = check_mirrors()
        if drifted:
            for entry in drifted:
                print(entry, file=sys.stderr)
            print(
                "run scripts/sync-plugin-bundle.py to regenerate plugin bundle mirrors",
                file=sys.stderr,
            )
            return 1
        print(f"plugin bundle mirrors match canonical source: {rel(CANONICAL_ROOT)}")
        return 0

    sync_mirrors()
    print(f"synced plugin bundle mirrors from canonical source: {rel(CANONICAL_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
