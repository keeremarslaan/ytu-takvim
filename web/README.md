# Student app — WIP

Run `npm ci`, then `npm run demo` for an API-free development demo with invented
QA courses. Open http://localhost:3000, select Fizik, and add QA001/QA003/QA005.
Test data is labelled visibly, accepted only in development, and removed on normal exit.

`npm run dev` without a package URL uses the local Python student API instead.
Copy `.env.local.example` to `.env.local` only if you need custom configuration.
Real timetable data is deliberately absent from this repository.

Checks: `npm test`, `npm run lint`, `npm run typecheck`.

Production uses static export: set `NEXT_PUBLIC_STUDENT_PACKAGE_URL` to a reviewed
release's public URL before `npm run build`. Output is `out/`; no Next/Python server
is needed to serve it. Remove generated demo/QA/pilot JSON from `public/` first.
Production rejects synthetic packages and undated previews.

See the [root README](../README.md) for architecture, limitations and production build
instructions. No hosting or public data release is configured. Owner apps, databases
and reports stay local.
