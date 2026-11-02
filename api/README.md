# Scheduler API — WIP

Python 3.13 / FastAPI / SQLAlchemy / SQLite, with local document extraction,
curriculum ingestion, review/export workflows and a legacy OR-Tools solver.
No real timetable documents, extracted schedules or databases are distributed.

## Install and check

```sh
uv sync --locked --all-groups
uv run ruff check .
uv run pytest
```

Ruff excludes `data/`, which holds private local processing scripts and artifacts.
Tests use isolated databases and invented data, including a PDF grid generated in memory.
To exercise migrations without changing your working database:

```sh
YTU_DATABASE_URL=sqlite:////tmp/ytu-migration-check.db uv run alembic upgrade head
```

## Run services locally

```sh
# Local owner service: maintenance, drafts, terms and release exports.
uv run uvicorn ytu_scheduler.local_main:app --host 127.0.0.1 --port 8001

# Optional legacy student API, in a separate terminal.
uv run ytu-api
```

The owner app uses localhost:3001; the legacy student app uses localhost:3000.
The student API omits maintenance routes. Do not deploy or reverse-proxy the owner
service. `.env.example` documents safe local defaults; no token is required by the
loopback-only owner entry point. The legacy bearer helper is for older internal routes.

## Catalog preparation

A fresh checkout has no imported curricula or offerings. The browser-only synthetic
demo in the root README works without this service. For owner development, the
reviewed program manifests are under `src/ytu_scheduler/data/`.

After reviewing the official directory against the manifest, synchronize curricula:

```sh
uv run python -m ytu_scheduler.catalog_cli sync --output data/catalog-sync-report.json
uv run python -m ytu_scheduler.catalog_cli coverage --output data/catalog-coverage.json
```

This requires network access, writes to your local database and retains source gaps.
It does not import term schedules. Never infer missing elective memberships.
`discover --directory-html path/to/directory.html --output data/proposed-programs.json`
can prepare directory changes for review. An offline `sync --cache-dir data/bologna`
uses the owner's previously reviewed source cache, which is not committed.

## Timetable preparation

See the [local owner setup](../local-owner/README.md),
[interchange schema](../docs/schedule-document.schema.json) and
[example JSON](../README.md#example-timetable-json).
Schema validation can run without a populated database:

```sh
uv run python -m ytu_scheduler.schedule_document_cli validate ../docs/examples/schedule-v1.json --output /tmp/ytu-example-validation.json
```

Source and catalog review precede any real export. Keep originals, drafts, owner
reports, source archives and exports private. The student static build consumes a
reviewed student-data package; it never exposes this local maintenance service.
