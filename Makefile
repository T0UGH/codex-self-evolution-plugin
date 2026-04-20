PYTHON ?= /Users/haha/hermes-agent/venv/bin/python3.11
IMAGE ?= codex-self-evolution-e2e
ENV_FILE ?= .env.provider

.PHONY: test docker-build docker-run docker-e2e preflight e2e-local provider-smoke-minimax provider-smoke-openai provider-smoke-anthropic

test:
	$(PYTHON) -m pytest -q

preflight:
	$(PYTHON) -m codex_self_evolution.cli compile-preflight --state-dir data

e2e-local:
	bash scripts/docker-e2e.sh

provider-smoke-minimax:
	@if [ -f "$(ENV_FILE)" ]; then set -a; . "$(ENV_FILE)"; set +a; fi; \
	$(PYTHON) scripts/provider-smoke-test.py --provider minimax

provider-smoke-openai:
	@if [ -f "$(ENV_FILE)" ]; then set -a; . "$(ENV_FILE)"; set +a; fi; \
	$(PYTHON) scripts/provider-smoke-test.py --provider openai-compatible

provider-smoke-anthropic:
	@if [ -f "$(ENV_FILE)" ]; then set -a; . "$(ENV_FILE)"; set +a; fi; \
	$(PYTHON) scripts/provider-smoke-test.py --provider anthropic-style

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run --rm $(IMAGE)

docker-e2e: docker-build docker-run
