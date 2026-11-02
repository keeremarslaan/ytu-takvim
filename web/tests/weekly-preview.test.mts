import test from 'node:test';
import assert from 'node:assert/strict';
import { weeklyPreview } from './helpers/weekly-preview.mts';
import { parseStudentPackage, offeredCourses } from '../src/lib/student-package.ts';
import { solveSchedules, meetingsConflict } from '../src/lib/browser-solver.ts';
import { dashboardResults } from '../src/lib/dashboard-package.ts';
import { calendarExport } from '../src/lib/calendar-export.ts';
const opts = { development: true, allowWeeklyPreview: true };
const fixture = () => parseStudentPackage(weeklyPreview(), opts);
const request = (courseIds = [1, 2]) => ({ programIds: [25], courseIds, locks: {}, blocked: [], preference: 'compact' as const });

test('undated preview requires explicit development opt-in', () => {
  for (const options of [{}, { development: true }, { allowWeeklyPreview: true }, { development: false, allowWeeklyPreview: true }]) assert.throws(() => parseStudentPackage(weeklyPreview(), options));
  const raw = weeklyPreview(); raw.purpose = 'release';
  assert.throws(() => parseStudentPackage(raw, opts));
});
test('weekly preview cannot mix dated and undated meetings', () => {
  const raw = weeklyPreview(); raw.groups[0].meetings[0].effective_starts_on = '2026-09-21';
  assert.throws(() => parseStudentPackage(raw, opts));
});
test('shared courses deduplicate while groups retain per-major restrictions', () => {
  const p = fixture();
  assert.equal(offeredCourses(p, [25]).find(c => c.catalog_course_id === 1)!.groups.length, 1);
  assert.equal(offeredCourses(p, [25, 9]).find(c => c.catalog_course_id === 1)!.groups.length, 2);
});
test('named infeasibility describes invented courses and actual overlap', async () => {
  const p = fixture(), result = await solveSchedules(p, request());
  assert.equal(result.status, 'infeasible');
  const message = dashboardResults(p, result).message ?? '';
  for (const text of ['DEMO1001', 'DEMO2001', 'Salı 11:00–12:00']) assert.ok(message.includes(text), text);
});
test('double-major union restores valid complete alternatives', async () => {
  const result = await solveSchedules(fixture(), { ...request(), programIds: [25, 9] });
  assert.equal(result.status, 'complete'); assert.equal(result.found, 3);
  for (const a of result.alternatives) {
    assert.equal(a.groups.length, 2);
    assert.ok(!a.groups[0].meetings.some(m => a.groups[1].meetings.some(n => meetingsConflict(m, n))));
  }
});
test('locks remain hard constraints and ineligible locks reject', async () => {
  const p = fixture(), wrong = offeredCourses(p, [9])[0].groups[0];
  await assert.rejects(solveSchedules(p, { ...request([1]), locks: { 1: wrong.id } }));
  const result = await solveSchedules(p, { ...request(), programIds: [25, 9], locks: { 1: wrong.id } });
  assert.equal(result.status, 'complete');
  assert.ok(result.alternatives.every(a => a.groups.some(g => g.id === wrong.id)));
});
test('weekends preserve full duration and blocked hours reject the group', async () => {
  const p = fixture(), group = offeredCourses(p, [25]).find(c => c.catalog_course_id === 3)!.groups[0];
  const result = await solveSchedules(p, request([3]));
  assert.equal(result.status, 'complete'); assert.equal(result.alternatives[0].metrics.teachingDays, 1);
  assert.equal((await solveSchedules(p, { ...request([3]), blocked: [group.meetings[0]] })).status, 'infeasible');
});
test('untimed options stay visible without fabricated schedules', async () => {
  const raw = weeklyPreview(); raw.groups[0].time_status = 'tba'; raw.groups[0].meetings = [];
  const p = parseStudentPackage(raw, opts);
  assert.equal(offeredCourses(p, [25])[0].groups[0].time_status, 'tba');
  assert.equal((await solveSchedules(p, request([1]))).status, 'infeasible');
});
test('calendar downloads remain unavailable for undated previews', async () => {
  const p = fixture(), result = await solveSchedules(p, request([3]));
  assert.throws(() => calendarExport(p, result.alternatives[0].groups), /tarih/);
});
