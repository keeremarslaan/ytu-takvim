# YTÜ Takvim — local owner tools (WIP)

This application prepares timetable data on the owner's computer. Keep its source
in the repository, but never deploy its service or include owner data in the student site.

From `api/`, install dependencies with `uv sync --locked --all-groups`, then run:

```sh
uv run uvicorn ytu_scheduler.local_main:app --host 127.0.0.1 --port 8001
```

From this directory, run `npm ci` and `npm run dev`. Open http://localhost:3001/.
A fresh checkout has no imported university schedules or populated catalog.

The local API checks client address, Host and Origin. The student API entry point
omits maintenance routes. Do not reverse-proxy or deploy the local service.

Implemented: source/JSON review, immutable drafts, saved baseline comparisons,
term/date management, validated release export, export history and downloads.
Saving a draft or creating an export does not publish data. Keep source archives,
owner reports and both local SQLite databases private and backed up.

Checks: `npm run lint` and `npm run typecheck`.
See the [root README](../README.md) and
[interchange schema](../docs/schedule-document.schema.json).
