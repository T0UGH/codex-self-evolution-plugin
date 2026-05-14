PYTHON ?= /Users/haha/hermes-agent/venv/bin/python3.11

.PHONY: test

test:
	$(PYTHON) -m pytest -q
