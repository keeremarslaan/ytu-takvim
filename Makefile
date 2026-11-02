.PHONY: api-dev web-dev demo test

api-dev:
	cd api && uv run ytu-api

web-dev:
	cd web && npm run dev

demo:
	cd web && npm run demo

test:
	cd api && uv run ruff check . && uv run pytest
	cd web && npm test && npm run lint && npm run typecheck
	cd local-owner && npm run lint && npm run typecheck
