.PHONY: help setup hooks test lint typecheck check snapshot backtest-quick backtest weekly daily worker pause resume status backup restore-drill docker-build dashboard

help:  ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup:  ## Install dependencies (including the dashboard extra)
	uv sync --extra dashboard

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

weekly:  ## Run this week's advice now (needs GUARD_DATA_DIR; ARGS="--slot <a date this week> --isa-lump-sum 2000")
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

backup:  ## Snapshot the data directory (needs GUARD_DATA_DIR and GUARD_BACKUP_DIR)
	uv run python -m guard.storage.backup backup

restore-drill:  ## Restore the latest backup into a temp dir, check it and run status (needs GUARD_BACKUP_DIR)
	ops/restore_drill.sh

docker-build:  ## Build the image (unverified: Docker was not available when this was written)
	docker build -t portfolio-guard .

test-alert:  ## Send a test message through the real alert channels (needs the Telegram/ntfy secrets)
	uv run python -m guard.worker test-alert

dashboard:  ## Run the dashboard on 127.0.0.1:8501 (needs GUARD_DATA_DIR and an access setting, see README)
	uv run --extra dashboard streamlit run src/guard/ui/app.py --server.address 127.0.0.1 --server.headless true
