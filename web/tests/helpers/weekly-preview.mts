import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

/** Invented test scenario; never reads owner data or real timetable exports. */
export function weeklyPreview() {
  const p = JSON.parse(readFileSync(new URL('../fixtures/student-package.json', import.meta.url), 'utf8'));
  p.purpose = 'weekly_preview';
  p.export_id = createHash('sha256').update('invented weekly regression v1').digest('hex');
  p.term.starts_on = p.term.ends_on = null;
  p.courses = [1, 2, 3].map(id => ({ catalog_course_id: id, code: `DEMO${id}001`, name: `Invented course ${id}`, prerequisite_text: null }));
  for (const program of p.programs) program.course_permissions = p.courses.map((c: { catalog_course_id: number }) => ({ catalog_course_id: c.catalog_course_id, status: 'eligible' }));
  const template = structuredClone(p.groups[0]);
  p.groups = [];
  for (const [course, label, major, day, start, end] of [
    [1, 'A', 25, 2, '11:00:00', '12:00:00'],
    [1, 'B', 9, 3, '11:00:00', '12:00:00'],
    [2, 'A', 25, 2, '11:00:00', '12:00:00'],
    [2, 'B', 9, 4, '14:00:00', '15:00:00'],
    [3, 'A', 25, 6, '08:00:00', '09:00:00'],
  ] as const) {
    const g = structuredClone(template);
    g.id = createHash('sha256').update(`invented-${course}-${label}`).digest('hex');
    g.catalog_course_id = course; g.label = label;
    g.instructor = null; g.evidence = { page: 1, quote: 'Invented regression scenario' };
    g.permissions = { allowed_bologna_program_ids: [major], excluded_bologna_program_ids: [], unresolved: false };
    g.eligible_bologna_program_ids = [major];
    g.meetings = [{ weekday: day, starts_at: start, ends_at: end, room: 'Demo room', delivery_mode: 'in_person', effective_starts_on: null, effective_ends_on: null }];
    p.groups.push(g);
  }
  return p;
}
