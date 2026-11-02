import { offeredCourses, type StudentPackage, type CourseClassification } from './student-package.ts';
import type { SolverResult } from './browser-solver.ts';

/** Preserve package permissions; presentation IDs never become solver group IDs. */
export function dashboardOfferings(data: StudentPackage, programIds: number[]) {
  return offeredCourses(data, programIds).map(c => {
    const roles = (data.classifications ?? []).filter(r => r.courseId === c.catalog_course_id && programIds.includes(r.programId) && c.groups.some(g => g.eligible_bologna_program_ids.includes(r.programId))).flatMap(r => r.roles.map(role => ({ ...role, program: data.programs.find(p => p.id === r.programId)!.name })));
    return ({
    id: c.catalog_course_id, offering_ids: [c.catalog_course_id], term_id: 1,
    course_id: c.catalog_course_id, course_code: c.code, course_name: c.name,
    departments: [], majors: data.programs.filter(p => programIds.includes(p.id) && c.groups.some(g => g.eligible_bologna_program_ids.includes(p.id))).map(p => p.name),
    status: 'eligible', prerequisite_text: c.prerequisite_text,
    curriculum_roles: roles,
    categories: [...new Set(roles.map(r => r.category).filter((c): c is NonNullable<CourseClassification["category"]> => c !== null))],
    compulsory_placements: roles.filter(r => r.category === "compulsory" && r.year !== null && r.season !== null).map(r => ({ program: r.program, year: r.year!, season: r.season! })),
    options: c.groups.map(g => ({ id: g.id, group_label: g.label, language: g.language,
      instructor: g.instructor, time_status: g.time_status, meetings: g.meetings })),
  }); });
}

export function dashboardResults(data: StudentPackage, result: SolverResult) {
  const message = result.status === 'infeasible' ? 'Bu seçimlerle uygun program yok. Dersleri, grup kilitlerini veya engellenen saatleri gözden geçirin.'
    : result.status === 'limited' ? 'Arama sınırına ulaşıldı. Bulunan seçenekler gösteriliyor; en iyi seçenek garanti değildir. Sonuç bulunmaması çözüm olmadığı anlamına gelmez.'
    : result.status === 'cancelled' ? 'Arama iptal edildi.' : null;
  return {
    schedules: result.alternatives.map((a, i) => ({ rank: i + 1, score: 0,
      selections: a.groups.map(g => {
        const c = data.courses.find(c => c.catalog_course_id === g.catalog_course_id)!;
        return { course_id: c.catalog_course_id, course_code: c.code, course_name: c.name,
          option_id: g.id, option_label: g.label, meetings: g.meetings };
      }),
      metrics: { days_on_campus: a.metrics.teachingDays, total_gap_minutes: Math.round(a.metrics.gapMinutes),
        early_meetings: a.metrics.earlyMeetings, late_meetings: a.metrics.lateMeetings }, warnings: [],
    })),
    explored_solutions: result.found, truncated: result.status === 'limited',
    message: [message, ...result.reasons].filter(Boolean).join(' ') || null, conflicts: [],
  };
}

/** Apply all filters to the same curriculum role, avoiding cross-major combinations. */
export function matchesCurriculumRole(roles: (CourseClassification & { program: string })[], filter: { major: string; category: string; year: string; season: string }) {
  if (filter.category === "all" && filter.year === "all" && filter.season === "all") return true;
  return roles.some(r => (filter.major === "all" || r.program === filter.major)
    && (filter.category === "all" || r.category === filter.category)
    && (filter.year === "all" || r.year === Number(filter.year))
    && (filter.season === "all" || r.season === filter.season));
}
