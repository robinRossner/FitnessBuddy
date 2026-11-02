# Named commands for FitnessBuddy. `make` lists them. Backend commands run in backend/ via uv.
SHELL := /bin/bash
.DEFAULT_GOAL := help

SIM ?= iPhone 17 Pro
GROUP ?=
DEPLOY_ARGS ?=
IOS_DEST = -destination 'platform=iOS Simulator,name=$(SIM)'
XCODEBUILD = set -o pipefail; cd ios && xcodebuild -project FitnessBuddy.xcodeproj -scheme FitnessBuddy $(IOS_DEST)
XCPRETTY := $(if $(shell command -v xcbeautify 2>/dev/null),| xcbeautify,)

.PHONY: help test test-fast test-group test-changed lint format typecheck \
	ios-gen ios-build ios-test-fast ios-test check deploy

help: ## list the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

test: ## full backend suite
	cd backend && uv run pytest

test-fast: ## backend tests that need no database (-m "not db")
	cd backend && uv run pytest -m "not db"

test-group: ## one backend group: make test-group GROUP=engine
	@[ -n "$(GROUP)" ] || { echo "usage: make test-group GROUP=engine|advisor|garmin|importers|api|gyms|sync|analytics" >&2; exit 2; }
	cd backend && uv run pytest tests/$(GROUP)

test-changed: ## backend groups affected by changes vs origin/main (BASE=ref to override)
	scripts/test-changed.sh

lint: ## ruff check + ruff format --check
	cd backend && uv run ruff check . && uv run ruff format --check .

format: ## ruff check --fix + ruff format
	cd backend && uv run ruff check --fix . && uv run ruff format .

typecheck: ## pyright (placeholder)
	@echo "pyright: added in wave 3"

ios-gen: ## xcodegen generate (ios/FitnessBuddy.xcodeproj)
	cd ios && xcodegen generate

ios-build: ios-gen ## Debug build for the simulator (SIM="iPhone 17 Pro")
	$(XCODEBUILD) -configuration Debug build $(XCPRETTY)

ios-test-fast: ios-gen ## iOS tests, Fast test plan
	$(XCODEBUILD) -testPlan Fast test $(XCPRETTY)

ios-test: ios-gen ## iOS tests, Full test plan
	$(XCODEBUILD) -testPlan Full test $(XCPRETTY)

check: lint test ios-test-fast ## lint + backend suite + iOS Fast plan

deploy: ## deploy/push.sh $(DEPLOY_ARGS), e.g. DEPLOY_ARGS="ubuntu@HOST --dry-run"
	deploy/push.sh $(DEPLOY_ARGS)
