import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseStudentPackage, offeredCourses } from "../src/lib/student-package.ts";
const fixture = () => JSON.parse(readFileSync(new URL("./fixtures/student-package.json", import.meta.url), "utf8"));
const dev = { development: true, allowTest: true };

test("actual exporter artifact loads and preserves meetings, dates and stable groups", () => {
  const raw = fixture(), data = parseStudentPackage(raw, dev);
  assert.equal(data.programs.length, 56);
  const courses = offeredCourses(data, [25]);
  assert.equal(courses.length, 1);
  assert.equal(courses[0].groups[0].id, raw.groups[0].id);
  assert.deepEqual(courses[0].groups[0].meetings[0].effective_starts_on, raw.groups[0].meetings[0].effective_starts_on);
  assert.equal(offeredCourses(data, [9]).length, 0);
});
test("test packages require both explicit opt-in and development", () => {
  for (const options of [{}, { allowTest: true }, { development: true }, { development: false, allowTest: true }]) assert.throws(() => parseStudentPackage(fixture(), options));
});
test("real packages load without test opt-in", () => { const p = fixture(); p.purpose = "release"; assert.ok(parseStudentPackage(p)); });
test("double-major permissions cannot combine different majors' course and group grants", () => {
  const p = fixture();
  p.groups.forEach((g: Record<string, unknown>) => { g.permissions = { allowed_bologna_program_ids: [9], excluded_bologna_program_ids: [], unresolved: false }; g.eligible_bologna_program_ids = []; });
  assert.equal(offeredCourses(parseStudentPackage(p, dev), [25, 9]).length, 0);
  p.groups[0].eligible_bologna_program_ids = [9];
  assert.throws(() => parseStudentPackage(p, dev));
});
test("shared courses deduplicate and retain permitted groups", () => {
  const p = fixture();
  p.programs.find((x: {bologna_program_id: number}) => x.bologna_program_id === 9).course_permissions = structuredClone(p.programs.find((x: {bologna_program_id: number}) => x.bologna_program_id === 25).course_permissions);
  p.groups.forEach((g: {eligible_bologna_program_ids: number[]}) => g.eligible_bologna_program_ids = [9, 25]);
  const data = parseStudentPackage(p, dev);
  assert.equal(offeredCourses(data, [9, 25]).length, 1);
  assert.equal(offeredCourses(data, [9, 25])[0].groups.length, p.groups.length);
});
test("unknown, duplicate and unavailable major selections fail closed", () => {
  const data = parseStudentPackage(fixture(), dev);
  for (const ids of [[], [25, 25], [999], [52], [9, 25, 52]]) assert.throws(() => offeredCourses(data, ids));
});
test("TBA groups remain visible without invented meetings", () => {
  const p = fixture(); p.groups[0].time_status = "tba"; p.groups[0].meetings = [];
  assert.equal(offeredCourses(parseStudentPackage(p, dev), [25])[0].groups[0].time_status, "tba");
});
for (const [name, mutate] of Object.entries({
  version: (p: ReturnType<typeof fixture>): void => { p.schema_version = "2.0"; },
  duplicate: (p: ReturnType<typeof fixture>): void => { p.groups.push(p.groups[0]); },
  withdrawn: (p: ReturnType<typeof fixture>): void => { p.sources[0].withdrawn = true; },
  source: (p: ReturnType<typeof fixture>): void => { p.groups[0].draft_id = "f".repeat(64); },
  exclusion: (p: ReturnType<typeof fixture>): void => { p.groups[0].permissions.excluded_bologna_program_ids = [25]; },
  unresolved: (p: ReturnType<typeof fixture>): void => { p.groups[0].permissions.unresolved = true; },
  course: (p: ReturnType<typeof fixture>): void => { p.groups[0].catalog_course_id = 999; },
  date: (p: ReturnType<typeof fixture>): void => { p.term.starts_on = "2026-02-30"; },
  interval: (p: ReturnType<typeof fixture>): void => { p.groups[0].meetings[0].ends_at = "00:00:00"; },
  scheduled: (p: ReturnType<typeof fixture>): void => { p.groups[0].meetings = []; },
  coverage: (p: ReturnType<typeof fixture>): void => { p.coverage = []; },
})) test(`rejects invalid ${name}`, () => { const p = fixture(); mutate(p); assert.throws(() => parseStudentPackage(p, dev)); });

test("rejects overlapping meetings within one group", () => {
  const p = fixture(); p.groups[0].meetings.push(structuredClone(p.groups[0].meetings[0]));
  assert.throws(() => parseStudentPackage(p, dev));
});
test("rejects date ranges without the stated weekday", () => {
  const p = fixture(); p.groups[0].meetings[0].effective_starts_on = "2026-09-22";
  p.groups[0].meetings[0].effective_ends_on = "2026-09-22";
  assert.throws(() => parseStudentPackage(p, dev));
});
test("equivalent fractional times cannot form a positive interval", () => {
  const p = fixture(); p.groups[0].meetings[0].starts_at = "09:00:00";
  p.groups[0].meetings[0].ends_at = "09:00:00.0";
  assert.throws(() => parseStudentPackage(p, dev));
});
