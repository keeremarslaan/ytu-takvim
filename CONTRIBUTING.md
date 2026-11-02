# Contributing

This is a portfolio WIP. Keep changes focused, explain the behavior being changed,
and run `make test` after installing the three projects' dependencies.

## Data policy

Never commit university timetable PDFs/spreadsheets, extracted schedule JSON,
student release packages, owner reports, source archives, databases, backups,
credentials or local handover notes. Do not force-add ignored files.

Only the root README, this contributing guide, and the three component READMEs are
public Markdown. Agent instructions, prompts, product/design notes and QA journals
stay local. Interactive analysis history such as `.Rhistory` also stays local.

Use invented courses and meeting times for tests and screenshots. Public curriculum
and program-directory manifests are separate from term schedules. Existing synthetic
fixtures live under `web/tests/fixtures/` and `docs/examples/`; PDF tests draw their
own fictional grids at runtime.

Run `python3 scripts/check_public_repository.py` after staging changes. It checks the
Git index for forbidden paths, file types and common credential patterns. Review
`git diff --cached` as well; an automated check cannot establish data provenance.

The local owner app/API must remain local. Production must continue rejecting
synthetic test packages and undated weekly previews. Do not relabel a fixture as a
real release or weaken validation to make a demo build.

## Before a real application release

Complete the README roadmap, validate a real reviewed package, verify the static
artifact contains only intended public assets, and test the live deployment.
Publishing this source repository is a separate milestone from releasing timetable data.
