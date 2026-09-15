.PHONY: install lock test lint fmt check ingest coverage run clean help

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## create the venv and install from uv.lock
	uv sync --frozen --all-extras

lock:  ## regenerate uv.lock after changing pyproject.toml
	uv lock

test:  ## run the test suite
	uv run pytest -v

lint:  ## ruff check + format check (same as CI)
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## autofix formatting and lint
	uv run ruff format .
	uv run ruff check --fix .

check: lint test  ## what CI runs — do this before every push

ingest:  ## pull the universe from EDGAR
	uv run factline ingest

coverage:  ## what is actually in the lake
	uv run factline coverage

run:  ## the full pipeline, as the timer runs it
	./deploy/run-pipeline.sh

clean:  ## drop derived data; raw/ and the http cache are kept
	rm -rf data/clean data/quarantine
	@echo "clean/ rebuilds from raw/ with no network access"
