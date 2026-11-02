export type CourseClassification = { category: "compulsory" | "professional_elective" | "social_elective" | null; year: number | null; season: "fall" | "spring" | "summer" | null };
/** Student-facing projection. Original source evidence stays in the downloaded artifact. */
export type StudentProgram = { id: number; name: string; curriculum_available: boolean; curriculum_ready: boolean };
export type PackageMeeting = { weekday: number; starts_at: string; ends_at: string; room: string | null; delivery_mode: string; effective_starts_on: string | null; effective_ends_on: string | null };
export type PackageGroup = { id: string; catalog_course_id: number; label: string; instructor: string | null; language: string | null; time_status: string; meetings: PackageMeeting[]; eligible_bologna_program_ids: number[] };
export type PackageCourse = { catalog_course_id: number; code: string; name: string; prerequisite_text: string | null };
export type StudentPackage = { classifications?: { programId: number; courseId: number; roles: CourseClassification[] }[]; export_id: string; purpose: string; term: { code: string; academic_year: string; season: string; starts_on: string | null; ends_on: string | null }; programs: StudentProgram[]; courses: PackageCourse[]; groups: PackageGroup[] };

type Obj = Record<string, unknown>;
function check(condition: unknown): asserts condition { if (!condition) throw new Error("Ders verisi geçersiz veya desteklenmiyor. Lütfen veri sorumlusuna bildirin."); }
function obj(v: unknown): Obj { check(v !== null && typeof v === "object" && !Array.isArray(v)); return v as Obj; }
function arr(v: unknown): unknown[] { check(Array.isArray(v)); return v; }
function str(v: unknown): string { check(typeof v === "string" && v.trim().length > 0); return v; }
function nullable(v: unknown): string | null { return v === null ? null : str(v); }
function id(v: unknown): number { check(Number.isSafeInteger(v) && (v as number) > 0); return v as number; }
function ids(v: unknown): number[] { const a = arr(v).map(id); check(new Set(a).size === a.length); return a; }
function hash(v: unknown): string { const s = str(v); check(/^[a-f0-9]{64}$/.test(s)); return s; }
function date(v: unknown): string { const s = str(v); check(/^\d{4}-\d{2}-\d{2}$/.test(s) && !isNaN(Date.parse(s)) && new Date(s).toISOString().slice(0, 10) === s); return s; }
function time(v: unknown): string { const s = str(v); check(/^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?$/.test(s)); return s; }
function seconds(value: string) { const [h, m, s] = value.split(":").map(Number); return h * 3600 + m * 60 + s; }
function firstMeetingDay(start: string, weekday: number) { const day = Date.parse(start) / 86400000; return day + (weekday - ((day + 3) % 7 + 1) + 7) % 7; }
function unique<T>(items: T[], key: (item: T) => unknown) { check(new Set(items.map(key)).size === items.length); }

export function parseStudentPackage(input: unknown, options: { development?: boolean; allowTest?: boolean; allowWeeklyPreview?: boolean } = {}): StudentPackage {
  const p = obj(input);
  check(p.schema_version === "1.0");
  const weekly = p.purpose === "weekly_preview" && options.development === true && options.allowWeeklyPreview === true;
  const parseDate = (value: unknown) => { if (weekly) { check(value === null); return null; } return date(value); };
  check(weekly || p.purpose === "release" || (p.purpose === "test" && options.development === true && options.allowTest === true));
  const export_id = hash(p.export_id);
  const t = obj(p.term);
  check(t.timezone === "Europe/Istanbul" && ["fall", "spring", "summer"].includes(str(t.season)));
  const term = { code: str(t.code), academic_year: str(t.academic_year), season: str(t.season), starts_on: parseDate(t.starts_on), ends_on: parseDate(t.ends_on) };
  check(weekly || (term.starts_on !== null && term.ends_on !== null && term.starts_on <= term.ends_on));
  const directory = arr(obj(p.student_directory).programs).map((v) => { const d = obj(v); return { id: id(d.bologna_program_id), name: str(d.name) }; });
  unique(directory, d => d.id);
  const catalog = arr(p.courses).map((v) => { const c = obj(v); return { catalog_course_id: id(c.catalog_course_id), code: str(c.code), name: str(c.name), prerequisite_text: nullable(c.prerequisite_text) }; });
  unique(catalog, c => c.catalog_course_id);
  const courseIds = new Set(catalog.map(c => c.catalog_course_id));
  const programData = arr(p.programs).map((v) => {
    const d = obj(v); const bid = id(d.bologna_program_id);
    check(directory.some(x => x.id === bid));
    if (d.curriculum !== null) { const c = obj(d.curriculum); id(c.id); hash(c.sha256); check(!isNaN(Date.parse(str(c.verified_at)))); }
    const classifications = arr(d.course_permissions).map(v => {
      const c = obj(v);
      const roles = c.classifications === undefined ? [] : arr(c.classifications).map(v => {
        const r = obj(v);
        check(r.category === null || ["compulsory", "professional_elective", "social_elective"].includes(str(r.category)));
        check(r.year === null || (Number.isSafeInteger(r.year) && Number(r.year) >= 1 && Number(r.year) <= 20));
        check(r.season === null || ["fall", "spring", "summer"].includes(str(r.season)));
        check((r.year === null) === (r.season === null));
        return { category: r.category, year: r.year, season: r.season } as CourseClassification;
      });
      return { programId: bid, courseId: id(c.catalog_course_id), roles };
    });
    const permissions = arr(d.course_permissions).map((v) => { const c = obj(v); const cid = id(c.catalog_course_id); check(courseIds.has(cid) && ["eligible", "approved_exception"].includes(str(c.status))); return cid; });
    unique(permissions, x => x);
    return { bid, available: d.curriculum !== null, permissions, classifications };
  });
  unique(programData, d => d.bid);
  const coverage = arr(p.coverage).map((v) => { const c = obj(v); const bid = id(c.bologna_program_id); check(directory.some(d => d.id === bid)); check(["ready", "incomplete", "missing"].includes(str(c.status))); arr(c.issues).forEach(str); check(c.timetable_completeness === "not_established"); return { bid, status: c.status }; });
  unique(coverage, c => c.bid);
  check(directory.every(d => coverage.some(c => c.bid === d.id)));
  const sources = arr(p.sources).map((v) => { const s = obj(v); check(typeof s.withdrawn === "boolean"); hash(s.document_sha256); hash(s.sha256); str(s.revision); return { draft: hash(s.draft_id), key: str(s.key), publisher: str(s.publisher), withdrawn: s.withdrawn }; });
  unique(sources, s => JSON.stringify([s.publisher, s.key]));
  const groups = arr(p.groups).map((v): PackageGroup => {
    const g = obj(v); const cid = id(g.catalog_course_id); check(courseIds.has(cid));
    check(sources.some(s => !s.withdrawn && s.draft === g.draft_id && s.key === g.source_key && s.publisher === g.publisher));
    const restriction = obj(g.permissions); check(restriction.unresolved === false);
    const allowed = restriction.allowed_bologna_program_ids === null ? null : ids(restriction.allowed_bologna_program_ids);
    const excluded = ids(restriction.excluded_bologna_program_ids);
    check(allowed === null || (allowed.length > 0 && !allowed.some(b => excluded.includes(b))));
    const eligible = ids(g.eligible_bologna_program_ids);
    const expected = programData.filter(d => d.permissions.includes(cid) && (allowed === null || allowed.includes(d.bid)) && !excluded.includes(d.bid)).map(d => d.bid);
    check(expected.length === eligible.length && expected.every(b => eligible.includes(b)));
    const meetings = arr(g.meetings).map((v): PackageMeeting => {
      const m = obj(v); const weekday = id(m.weekday); check(weekday <= 7);
      const starts_at = time(m.starts_at), ends_at = time(m.ends_at);
      const effective_starts_on = parseDate(m.effective_starts_on), effective_ends_on = parseDate(m.effective_ends_on);
      check(seconds(starts_at) < seconds(ends_at));
      if (!weekly) {
        check(effective_starts_on !== null && effective_ends_on !== null && term.starts_on !== null && term.ends_on !== null);
        check(effective_starts_on <= effective_ends_on && effective_starts_on >= term.starts_on && effective_ends_on <= term.ends_on);
        check(firstMeetingDay(effective_starts_on, weekday) <= Date.parse(effective_ends_on) / 86400000);
      }
      check(["in_person", "online", "hybrid", "unknown"].includes(str(m.delivery_mode)));
      return { weekday, starts_at, ends_at, effective_starts_on, effective_ends_on, room: nullable(m.room), delivery_mode: str(m.delivery_mode) };
    });
    for (let i = 0; i < meetings.length; i++) {
      for (const b of meetings.slice(i + 1)) {
        const a = meetings[i];
        let sameDates = weekly;
        if (a.effective_starts_on && b.effective_starts_on && a.effective_ends_on && b.effective_ends_on) {
          const start = a.effective_starts_on > b.effective_starts_on ? a.effective_starts_on : b.effective_starts_on;
          const end = a.effective_ends_on < b.effective_ends_on ? a.effective_ends_on : b.effective_ends_on;
          sameDates = firstMeetingDay(start, a.weekday) <= Date.parse(end) / 86400000;
        }
        check(!(a.weekday === b.weekday && seconds(a.starts_at) < seconds(b.ends_at) && seconds(b.starts_at) < seconds(a.ends_at) && sameDates));
      }
    }
    check((g.time_status === "scheduled" && meetings.length > 0) || (g.time_status === "tba" && meetings.length === 0));
    return { id: hash(g.id), catalog_course_id: cid, label: str(g.label), instructor: nullable(g.instructor), language: nullable(g.language), time_status: str(g.time_status), meetings, eligible_bologna_program_ids: eligible };
  });
  unique(groups, g => g.id);
  unique(groups, g => JSON.stringify([g.catalog_course_id, g.label.toLocaleLowerCase()]));
  const programs = directory.map(d => ({ ...d, curriculum_available: programData.some(p => p.bid === d.id && p.available), curriculum_ready: coverage.some(c => c.bid === d.id && c.status === "ready") }));
  return { classifications: programData.flatMap(p => p.classifications), export_id, purpose: str(p.purpose), term, programs, courses: catalog, groups };
}

export function offeredCourses(data: StudentPackage, programIds: number[]) {
  check(programIds.length >= 1 && programIds.length <= 2 && new Set(programIds).size === programIds.length && programIds.every(id => data.programs.some(p => p.id === id && p.curriculum_available)));
  return data.courses.flatMap(course => {
    const groups = data.groups.filter(g => g.catalog_course_id === course.catalog_course_id && programIds.some(id => g.eligible_bologna_program_ids.includes(id)));
    return groups.length ? [{ ...course, groups }] : [];
  });
}

export async function loadStudentPackage(url: string, signal: AbortSignal) {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Ders verileri yüklenemedi. Lütfen yeniden deneyin.");
  return parseStudentPackage(await response.json(), {
    development: process.env.NODE_ENV === "development",
    allowTest: process.env.NEXT_PUBLIC_ALLOW_TEST_PACKAGE === "true",
    allowWeeklyPreview: process.env.NEXT_PUBLIC_ALLOW_WEEKLY_PREVIEW === "true",
  });
}
