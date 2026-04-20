PYTHON ?= /Users/haha/hermes-agent/venv/bin/python3.11
IMAGE ?= codex-self-evolution-e2e

.PHONY: test docker-build docker-run docker-e2e preflight e2e-local

test:
	$(PYTHON) -m pytest -q

preflight:
	$(PYTHON) -m codex_self_evolution.cli compile-preflight --state-dir data

e2e-local:
	bash scripts/docker-e2e.sh

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run --rm $(IMAGE)

docker-e2e: docker-build docker-run
