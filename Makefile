.PHONY: help setup hooks test lint typecheck check snapshot backtest-quick backtest weekly daily worker pause resume status

help:  ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup:  ## Install dependencies
	uv sync

hooks:  ## Install pre-commit and pre-push git hooks
	uv run pre-commit install

test:  ## Unit, golden and property tests
	uv run pytest -m "not live and not perf" --ignore=tests/integration

lint:  ## Ruff lint
	uv run ruff check src tests

typecheck:  ## Mypy
	uv run mypy src/guard

check: lint typecheck test  ## Everything required before a change is done

snapshot:  ## Run the daily portfolio snapshot job (needs GUARD_DATA_DIR)
	uv run python -m guard.ingest.snapshot_job

backtest-quick:  ## Phase 0 backtest, small grid (needs GUARD_DATA_DIR and MSCI file)
	uv run python -m guard.backtest.run --quick

backtest:  ## Phase 0 backtest, full grid (needs GUARD_DATA_DIR and MSCI file)
	uv run python -m guard.backtest.run

weekly:  ## Run this week's advice now (needs GUARD_DATA_DIR; ARGS="--slot 2026-11-11 --isa-lump-sum 2000")
	uv run python -m guard.worker run-now weekly $(ARGS)

daily:  ## Run the daily light check now (needs GUARD_DATA_DIR)
	uv run python -m guard.worker run-now daily

worker:  ## Run the scheduler: weekly advice, daily check and snapshot (needs GUARD_DATA_DIR)
	uv run python -m guard.worker

pause:  ## Stop all advice until resumed
	uv run python -m guard.worker pause

resume:  ## Allow advice again
	uv run python -m guard.worker resume

status:  ## Pause switch and the last successful run of each job
	uv run python -m guard.worker status
