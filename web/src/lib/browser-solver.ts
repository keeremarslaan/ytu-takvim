import { offeredCourses, type PackageCourse, type PackageGroup, type PackageMeeting, type StudentPackage } from "./student-package.ts";

export type BlockedTime = { weekday: number; starts_at: string; ends_at: string };
export type Preference = "compact" | "few_days" | "late_start" | "early_finish";
export type SolverRequest = { programIds: number[]; courseIds: number[]; locks: Record<number, string>; blocked: BlockedTime[]; preference: Preference };
export type Alternative = { groups: PackageGroup[]; metrics: { teachingDays: number; gapMinutes: number; earlyMeetings: number; lateMeetings: number }; key: string };
export type SolverResult = { status: "complete" | "infeasible" | "limited" | "cancelled"; alternatives: Alternative[]; visited: number; found: number; reasons: string[] };
export type SolverControl = { signal?: AbortSignal; maxNodes?: number; maxMs?: number; now?: () => number; yieldTask?: () => Promise<void> };
const DAY = 86400000;
const timeSeconds = (s: string) => s.split(":").reduce((n, v, i) => n + Number(v) * [3600, 60, 1][i], 0);
const epochDay = (s: string) => Date.parse(`${s}T00:00:00Z`) / DAY;
const weekday = (day: number) => ((day + 3) % 7 + 7) % 7 + 1;
const validTime = (s: string) => /^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?$/.test(s);
const overlap = (a: number, b: number, c: number, d: number) => a < d && c < b;

export function meetingsConflict(a: PackageMeeting, b: PackageMeeting) {
  if (a.weekday !== b.weekday || !overlap(timeSeconds(a.starts_at), timeSeconds(a.ends_at), timeSeconds(b.starts_at), timeSeconds(b.ends_at))) return false;
  if (!a.effective_starts_on || !a.effective_ends_on || !b.effective_starts_on || !b.effective_ends_on) return true;
  const start = Math.max(epochDay(a.effective_starts_on), epochDay(b.effective_starts_on));
  const end = Math.min(epochDay(a.effective_ends_on), epochDay(b.effective_ends_on));
  return start + (a.weekday - weekday(start) + 7) % 7 <= end;
}

const days = ["", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];
const clock = (s: string) => s.length === 8 && s.endsWith(":00") ? s.slice(0, 5) : s;
const slot = (m: BlockedTime) => `${days[m.weekday]} ${clock(m.starts_at)}–${clock(m.ends_at)}`;
const courseName = (c: PackageCourse) => `${c.code} (${c.name})`;
const groupName = (g: PackageGroup, request: SolverRequest) => `${g.label}${request.locks[g.catalog_course_id] === g.id ? " [kilitli]" : ""}`;
const blockedConflict = (m: PackageMeeting, b: BlockedTime) => b.weekday === m.weekday && overlap(timeSeconds(m.starts_at), timeSeconds(m.ends_at), timeSeconds(b.starts_at), timeSeconds(b.ends_at));

type Event = { day: number; start: number; end: number };
function events(group: PackageGroup): Event[] {
  return group.meetings.flatMap(m => {
    if (!m.effective_starts_on || !m.effective_ends_on) return [{ day: m.weekday, start: timeSeconds(m.starts_at), end: timeSeconds(m.ends_at) }];
    const result: Event[] = [];
    const start = epochDay(m.effective_starts_on), end = epochDay(m.effective_ends_on);
    for (let d = start + (m.weekday - weekday(start) + 7) % 7; d <= end; d += 7) result.push({ day: d, start: timeSeconds(m.starts_at), end: timeSeconds(m.ends_at) });
    return result;
  });
}
function compare(a: Alternative, b: Alternative, preference: Preference) {
  const m = a.metrics, n = b.metrics;
  const primary = preference === "few_days" ? m.teachingDays - n.teachingDays : preference === "late_start" ? m.earlyMeetings - n.earlyMeetings : preference === "early_finish" ? m.lateMeetings - n.lateMeetings : m.gapMinutes - n.gapMinutes;
  return primary || m.gapMinutes - n.gapMinutes || m.teachingDays - n.teachingDays || a.key.localeCompare(b.key);
}

/** Cooperative bounded search; only complete assignments enter the result list. */
export async function solveSchedules(data: StudentPackage, request: SolverRequest, control: SolverControl = {}): Promise<SolverResult> {
  const now = control.now ?? (() => performance.now());
  const start = now();
  let lastYield = start;
  const maxNodes = Math.min(100000, Math.max(1, control.maxNodes ?? 30000));
  const maxMs = Math.min(5000, Math.max(1, control.maxMs ?? 2000));
  const yieldTask = control.yieldTask ?? (() => new Promise<void>(resolve => setTimeout(resolve, 0)));
  const output: SolverResult = { status: "complete", alternatives: [], visited: 0, found: 0, reasons: [] };
  function limit() { return now() - start >= maxMs; }
  if (control.signal?.aborted) return { ...output, status: "cancelled" };
  if (!request.courseIds.length || request.courseIds.length > 40 || new Set(request.courseIds).size !== request.courseIds.length) throw new Error("1–40 farklı ders seçin.");
  if (!["compact", "few_days", "late_start", "early_finish"].includes(request.preference)) throw new Error("Geçersiz sıralama tercihi.");
  if (request.blocked.length > 50 || request.blocked.some(b => !Number.isInteger(b.weekday) || b.weekday < 1 || b.weekday > 7 || !validTime(b.starts_at) || !validTime(b.ends_at) || timeSeconds(b.starts_at) >= timeSeconds(b.ends_at))) throw new Error("Engellenen saat aralıklarını kontrol edin.");
  if (Object.keys(request.locks).some(cid => !request.courseIds.includes(Number(cid)))) throw new Error("Grup kilidi seçili bir derse ait olmalıdır.");
  const courses = offeredCourses(data, request.programIds);
  const domains = request.courseIds.map(cid => {
    const c = courses.find(c => c.catalog_course_id === cid);
    if (!c) throw new Error("Seçili ders bu bölümler için uygun değil. Dersleri yeniden seçin.");
    const lock = request.locks[cid];
    if (lock && !c.groups.some(g => g.id === lock)) throw new Error(`${c.code}: kilitli grup artık uygun değil.`);
    const eligible = c.groups.filter(g => !lock || g.id === lock);
    const scheduled = eligible.filter(g => g.time_status === "scheduled");
    const groups = scheduled.filter(g => !g.meetings.some(m => request.blocked.some(b => blockedConflict(m, b))));
    if (!groups.length) {
      if (!scheduled.length) output.reasons.push(`${courseName(c)}: ${lock ? `${groupName(eligible[0], request)} grubunun` : "uygun grupların"} ders saatleri henüz belli değil.`);
      else {
        const examples = scheduled.slice(0, 3).map(g => {
          const m = g.meetings.find(m => request.blocked.some(b => blockedConflict(m, b)))!;
          const b = request.blocked.find(b => blockedConflict(m, b))!;
          return `${groupName(g, request)}: ders ${slot(m)}, engellenen saat ${slot(b)}`;
        });
        output.reasons.push(`${courseName(c)}: ${lock ? "kilitli grup" : "saatleri belli tüm uygun gruplar"} engellenen saatlerle çakışıyor. ${examples.join("; ")}.${scheduled.length > 3 ? " Diğer gruplar da engelleniyor." : ""} Engellenen saatleri${lock ? " veya grup kilidini" : ""} gözden geçirin.`);
      }
    }
    return { course: c, groups: groups.sort((a, b) => a.id.localeCompare(b.id)) };
  }).sort((a, b) => a.groups.length - b.groups.length || a.course.catalog_course_id - b.course.catalog_course_id);
  if (output.reasons.length) return { ...output, status: "infeasible" };
  // Input ceilings keep preparation and each scoring step finite too.
  if ((data.term.ends_on && data.term.starts_on && epochDay(data.term.ends_on) - epochDay(data.term.starts_on) > 370) || domains.reduce((n, d) => n + d.groups.length, 0) > 2000 || domains.some(d => d.groups.some(g => g.meetings.length > 50))) return { ...output, status: "limited", reasons: ["Veri boyutu bu arama sınırını aşıyor. Daha az ders seçin."] };
  const expanded = new Map<string, Event[]>();
  let totalEvents = 0;
  for (const domain of domains) {
    for (const g of domain.groups) {
      const e = events(g);
      totalEvents += e.length;
      if (totalEvents > 100000 || limit()) return { ...output, status: "limited" };
      expanded.set(g.id, e);
      if (now() - lastYield >= 8) {
        await yieldTask(); lastYield = now();
        if (control.signal?.aborted) return { ...output, status: "cancelled" };
      }
    }
    await yieldTask();
    if (control.signal?.aborted) return { ...output, status: "cancelled" };
  }
  const chosen: PackageGroup[] = [];
  const indices = new Array(domains.length).fill(0);
  let depth = 0;
  while (depth >= 0) {
    if (control.signal?.aborted) return { ...output, status: "cancelled" };
    if (limit() || output.visited >= maxNodes) return { ...output, status: "limited" };
    if (indices[depth] >= domains[depth].groups.length) { indices[depth] = 0; depth--; chosen.pop(); continue; }
    const candidate = domains[depth].groups[indices[depth]++];
    output.visited++;
    const conflict = chosen.some(g => g.meetings.some(a => candidate.meetings.some(b => meetingsConflict(a, b))));
    if (!conflict) {
      chosen.push(candidate);
      if (depth === domains.length - 1) {
        const all = chosen.flatMap(g => expanded.get(g.id)!).sort((a, b) => a.day - b.day || a.start - b.start);
        const metrics = { teachingDays: 0, gapMinutes: 0, earlyMeetings: 0, lateMeetings: 0 };
        all.forEach((e, i) => {
          const previous = all[i - 1];
          if (!previous || previous.day !== e.day) metrics.teachingDays++;
          else metrics.gapMinutes += Math.max(0, e.start - previous.end) / 60;
          if (e.start < 10 * 3600) metrics.earlyMeetings++;
          if (e.end > 17 * 3600) metrics.lateMeetings++;
        });
        const groups = [...chosen].sort((a, b) => a.catalog_course_id - b.catalog_course_id);
        output.alternatives.push({ groups, metrics, key: groups.map(g => g.id).join(":") });
        output.alternatives.sort((a, b) => compare(a, b, request.preference));
        if (output.alternatives.length > 12) output.alternatives.pop();
        output.found++;
        chosen.pop();
      } else depth++;
    }
    if (output.visited % 64 === 0 || now() - lastYield >= 8) { await yieldTask(); lastYield = now(); }
  }
  if (!output.found) {
    output.status = "infeasible";
    // Explain only a proven incompatible pair, never an arbitrary failed DFS branch.
    // Diagnostics share the deadline and have an independent meeting-comparison cap.
    let checks = 0;
    diagnostic: for (let i = 0; i < domains.length; i++) {
      for (const right of domains.slice(i + 1)) {
        const left = domains[i];
        let example = "";
        let allConflict = true;
        pairs: for (const a of left.groups) for (const b of right.groups) {
          let clashes = false;
          for (const m of a.meetings) {
            for (const n of b.meetings) {
              if (++checks > 10000 || limit()) break diagnostic;
              if (checks % 64 === 0) {
                await yieldTask();
                if (control.signal?.aborted) return { ...output, status: "cancelled", reasons: [] };
                if (limit()) break diagnostic;
              }
              if (meetingsConflict(m, n)) {
                clashes = true;
                if (!example) example = `Örnek: ${left.course.code} ${groupName(a, request)} (${slot(m)}) ile ${right.course.code} ${groupName(b, request)} (${slot(n)}).`;
                break;
              }
            }
            if (clashes) break;
          }
          if (!clashes) { allConflict = false; break pairs; }
        }
        if (allConflict) {
          output.reasons.push(`${courseName(left.course)} ile ${courseName(right.course)}: mevcut kilitler ve engellenen saatler uygulandıktan sonra kalan tüm grup eşleşmeleri çakışıyor. ${example} Bu derslerden birini kaldırın veya varsa grup kilitlerini ve engellenen saatleri gözden geçirin.`);
          break diagnostic;
        }
      }
    }
    if (!output.reasons.length) output.reasons.push("Seçili derslerin grupları bu kilitler ve saat kısıtlarıyla birlikte yerleştirilemiyor. Tek bir ders çifti neden olarak belirlenemedi; dersleri, grup kilitlerini veya engellenen saatleri azaltarak yeniden deneyin.");
  }
  return output;
}
