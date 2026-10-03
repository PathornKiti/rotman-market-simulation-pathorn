# Shortcuts. Windows users: run the python commands shown in README.md directly.
PY ?= python

.PHONY: install test lint doctor monitor sim-% run-% live-%

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests

doctor:
	$(PY) -m ritc doctor

monitor:
	$(PY) -m ritc monitor

sim-%:            # make sim-equity
	$(PY) -m ritc sim $*

run-%:            # make run-equity  (dry run)
	$(PY) -m ritc run $* -v

live-%:           # make live-equity
	$(PY) -m ritc run $* --live
