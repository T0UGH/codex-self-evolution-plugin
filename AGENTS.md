# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11+ package using a `src/` layout. Core code lives in `src/codex_self_evolution/`: `cli.py` and `csep.py` expose command-line entry points, `hooks/` handles Codex lifecycle hooks, `review/` handles stop-review providers, `compiler/` promotes suggestions into memory/recall/skills, and `managed_skills/` publishes generated skills. Tests live in `tests/`, with fixtures under `tests/fixtures/`. Documentation and implementation notes live in `docs/` and `notes/`. README images are kept under `docs/assets/` as SVG sources plus committed PNG renderings. Plugin manifests are present in both `src/codex_self_evolution/plugin_bundle/.codex-plugin/` and `plugins/codex-self-evolution/.codex-plugin/`; keep them aligned when changing hook metadata.

## Build, Test, and Development Commands

- `python -m pip install -e .` installs the package locally with `codex-self-evolution` and `csep` console scripts.
- `python -m pip install -e '.[dev]'` adds contributor tooling such as `pytest`, `build`, and `twine`.
- `python -m pytest -q` runs the full test suite; CI runs this on Python 3.11 and 3.12.
- `make test PYTHON=python3.11` runs the same pytest command through the Makefile.
- `make preflight PYTHON=python3.11` runs `compile-preflight` against the local `data/` state directory.
- `python -m build` builds source and wheel distributions.
- `scripts/install.sh` installs the local CLI with `uv` and refreshes the Codex plugin cache.

## Coding Style & Naming Conventions

Use 4-space indentation, type hints where they clarify interfaces, and small stdlib-first modules. Runtime dependencies are intentionally empty; adding one requires updating `pyproject.toml` and README wording. Use snake_case for modules, functions, variables, and test names. Keep CLI output deterministic and machine-readable where existing commands already do so.

## Testing Guidelines

Use pytest. Name tests `test_*.py` and test functions `test_*`. Add focused coverage beside the behavior being changed, especially for storage state transitions, hook payload handling, CLI flags, and compiler promotion logic. Provider smoke tests require real credentials and are not part of public CI.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional-style subjects such as `docs: add supported artifacts visual` and `docs: record local enablement status`. Prefer `area: imperative summary`, keep commits scoped, and include tests or docs with behavior changes. PRs should describe the user-visible change, list verification commands, link issues when available, and include screenshots only for README or diagram updates.

## Security & Configuration Tips

Never commit provider secrets. Copy `.env.provider.example` to `~/.codex-self-evolution/.env.provider` for local use. The repository `data/` directory is local runtime state; keep only `data/.gitkeep` tracked.
