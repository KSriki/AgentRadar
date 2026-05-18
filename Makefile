.PHONY: test test-unit test-integration test-down lint help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: test-unit  ## Run unit tests (default)

test-unit:  ## Run unit tests only (fast, no docker)
	uv run pytest tests/unit/ -v

test-integration:  ## Run integration tests in isolated test plane
	docker compose -f docker-compose.yml -f docker-compose.test.yml build test-runner
	docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test-runner

test-integration-rebuild:  ## Force-rebuild test-runner image (when deps change)
	docker compose -f docker-compose.yml -f docker-compose.test.yml build --no-cache test-runner

test-down:  ## Tear down test plane completely
	docker compose -f docker-compose.yml -f docker-compose.test.yml down -v \
		postgres-test neo4j-test init-neo4j-test test-runner

lint:  ## Run pre-commit on all files
	uv run pre-commit run --all-files
