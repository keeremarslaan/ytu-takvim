"use client";

import { useDeferredValue, useEffect, useRef, useState } from "react";

import { dashboardOfferings, dashboardResults, matchesCurriculumRole } from "../lib/dashboard-package";
import { type StudentPackage, type CourseClassification } from "../lib/student-package";
import { solveSchedules, type BlockedTime, type Preference } from "../lib/browser-solver";
import { calendarExport } from "../lib/calendar-export";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const WEEKDAYS = ["", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];
const START_HOUR = 8;
const END_HOUR = 21;
const HOUR_HEIGHT = 64;
const COURSE_COLORS = ["gold", "blue", "green", "violet", "rose", "cyan"];

type Term = {
  id: number;
  code: string;
  academic_year: string;
  season: string;
};

type Meeting = {
  weekday: number;
  starts_at: string;
  ends_at: string;
  room: string | null;
  delivery_mode: string;
  effective_starts_on?: string | null;
  effective_ends_on?: string | null;
};

type Offering = {
  id: number;
  offering_ids: number[];
  term_id: number;
  course_id: number;
  course_code: string;
  course_name: string;
  departments: string[];
  majors: string[];
  status: string;
  prerequisite_text: string | null;
  curriculum_roles?: (CourseClassification & { program: string })[];
  categories: Array<"compulsory" | "professional_elective" | "social_elective">;
  compulsory_placements: Array<{
    program: string;
    year: number;
    season: "fall" | "spring" | "summer";
  }>;
  options: Array<{
    id: number | string;
    group_label: string;
    language: string | null;
    instructor: string | null;
    time_status: string;
    meetings: Meeting[];
  }>;
};

type ScheduleSelection = {
  course_id: number;
  course_code: string;
  course_name: string;
  option_id: number | string;
  option_label: string;
  meetings: Meeting[];
};

type ScheduleResult = {
  schedules: Array<{
    rank: number;
    score: number;
    selections: ScheduleSelection[];
    metrics: {
      days_on_campus: number;
      total_gap_minutes: number;
      early_meetings: number;
      late_meetings: number;
    };
    warnings: string[];
  }>;
  explored_solutions: number;
  truncated: boolean;
  message: string | null;
  conflicts: Array<{
    left_course_id: number;
    left_course_code: string;
    left_course_name: string;
    left_option_label: string;
    right_course_id: number;
    right_course_code: string;
    right_course_name: string;
    right_option_label: string;
    weekday: number;
    starts_at: string;
    ends_at: string;
  }>;
};

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function minutes(value: string) {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
}

function shortTime(value: string) {
  return value.slice(0, 5);
}

function courseColor(code: string) {
  const hash = Array.from(code).reduce((total, character) => total + character.charCodeAt(0), 0);
  return COURSE_COLORS[hash % COURSE_COLORS.length];
}

export function Dashboard({ programIds, onChangePrograms, packageData }: {
  packageData?: StudentPackage;
  programIds: number[];
  onChangePrograms: () => void;
}) {
  const programQuery = programIds.map((id) => `program_ids=${id}`).join("&");
  const [terms, setTerms] = useState<Term[]>(packageData ? [{ id: 1, ...packageData.term }] : []);
  const [activeTerm, setActiveTerm] = useState<number | null>(packageData ? 1 : null);
  const [offerings, setOfferings] = useState<Offering[]>(packageData ? dashboardOfferings(packageData, programIds) : []);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [results, setResults] = useState<ScheduleResult | null>(null);
  const [activeResult, setActiveResult] = useState(0);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [majorFilter, setMajorFilter] = useState("all");
  const [seasonFilter, setSeasonFilter] = useState("all");
  const [yearFilter, setYearFilter] = useState("all");
  const deferredSearch = useDeferredValue(search);
  const [scheduleBusy, setScheduleBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState(false);

  useEffect(() => {
    if (packageData) return;
    let cancelled = false;
    readJson<Term[]>("/terms")
      .then((nextTerms) => {
        if (cancelled) return;
        setTerms(nextTerms);
        setActiveTerm(nextTerms[0]?.id ?? null);
        setOnline(true);
      })
      .catch((reason: Error) => {
        if (cancelled) return;
        setOnline(false);
        setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, [packageData]);

  useEffect(() => {
    if (packageData || activeTerm === null) return;
    let cancelled = false;
    readJson<Offering[]>(`/schedules/offerings?term_id=${activeTerm}&${programQuery}`)
      .then((nextOfferings) => {
        if (cancelled) return;
        setOfferings(nextOfferings);
        setSelectedIds(new Set());
        setResults(null);
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTerm, programQuery, packageData]);

  const [locks, setLocks] = useState<Record<number, string>>({});
  const [blocked, setBlocked] = useState<BlockedTime[]>([]);
  const [preference, setPreference] = useState<Preference>("compact");
  const [blockDay, setBlockDay] = useState(1);
  const [blockStart, setBlockStart] = useState("09:00");
  const [blockEnd, setBlockEnd] = useState("10:00");
  const courseSearch = useRef<HTMLInputElement>(null);
  const solverController = useRef<AbortController | null>(null);
  const controlsKey = JSON.stringify([locks, blocked, preference]);
  function invalidate() { solverController.current?.abort(); setResults(null); setActiveResult(0); setScheduleBusy(selectedIds.size > 0); setError(null); }
  const selectionKey = Array.from(selectedIds).sort((left, right) => left - right).join(",");
  useEffect(() => {
    if (!selectionKey) return;
    let cancelled = false;
    if (packageData) {
      const aborter = new AbortController();
      solverController.current = aborter;
      const [savedLocks, savedBlocks, savedPreference] = JSON.parse(controlsKey) as [Record<number, string>, BlockedTime[], Preference];
      const courseIds = selectionKey.split(",").map(Number);
      solveSchedules(packageData, { programIds, courseIds,
        locks: Object.fromEntries(Object.entries(savedLocks).filter(([id, group]) => courseIds.includes(Number(id)) && group)),
        blocked: savedBlocks, preference: savedPreference }, { signal: aborter.signal })
        .then(result => { if (!aborter.signal.aborted) { setResults(dashboardResults(packageData, result)); setActiveResult(0); setScheduleBusy(false); } })
        .catch(reason => { if (!aborter.signal.aborted) { setError(String(reason)); setScheduleBusy(false); } });
      return () => aborter.abort();
    }
    const offeringIds = selectionKey.split(",").map(Number);
    readJson<ScheduleResult>("/schedules/generate-from-offerings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offering_ids: offeringIds, program_ids: programIds, max_results: 12 }),
    })
      .then((nextResults) => {
        if (cancelled) return;
        setResults(nextResults);
        setActiveResult(0);
        setScheduleBusy(false);
      })
      .catch((reason: Error) => {
        if (cancelled) return;
        setError(reason.message);
        setScheduleBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectionKey, programIds, packageData, controlsKey]);

  const availableMajors = Array.from(
    new Set(offerings.flatMap((offering) => offering.majors)),
  ).sort((left, right) => left.localeCompare(right, "tr"));
  const filteredOfferings = offerings.filter((offering) => {
    const query = deferredSearch.trim().toLowerCase();
    const haystack = `${offering.course_code} ${offering.course_name} ${offering.majors.join(" ")}`.toLowerCase();
    if (query && !haystack.includes(query)) return false;
    if (majorFilter !== "all" && !offering.majors.includes(majorFilter)) return false;
    if (packageData) return matchesCurriculumRole(offering.curriculum_roles ?? [], { major: majorFilter, category: categoryFilter, year: yearFilter, season: seasonFilter });
    if (categoryFilter !== "all" && !offering.categories.includes(categoryFilter as Offering["categories"][number])) {
      return false;
    }

    const placementFilterActive = seasonFilter !== "all" || yearFilter !== "all";
    if (!placementFilterActive || categoryFilter === "professional_elective" || categoryFilter === "social_elective") {
      return true;
    }
    const matchingCompulsoryPlacement = offering.compulsory_placements.some(
      (placement) =>
        (seasonFilter === "all" || placement.season === seasonFilter) &&
        (yearFilter === "all" || placement.year === Number(yearFilter)),
    );
    if (categoryFilter === "compulsory") return matchingCompulsoryPlacement;

    const hasElectiveRole = offering.categories.some((category) => category !== "compulsory");
    const hasCompulsoryRole = offering.categories.includes("compulsory");
    return hasElectiveRole || !hasCompulsoryRole || matchingCompulsoryPlacement;
  });

  const currentSchedule = selectedIds.size ? results?.schedules[activeResult] : undefined;
  const selectedOfferings = offerings.filter((offering) => selectedIds.has(offering.id));
  const selectedByCourse = new Map(
    currentSchedule?.selections.map((selection) => [selection.course_id, selection]) ?? [],
  );

  function toggleCourse(offeringId: number) {
    solverController.current?.abort();
    setResults(null);
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(offeringId)) next.delete(offeringId);
      else next.add(offeringId);
      if (!next.size) {
        setResults(null);
        setScheduleBusy(false);
      } else {
        setScheduleBusy(true);
      }
      return next;
    });
    setActiveResult(0);
    setError(null);
  }

  return (
    <div className={`planner-shell ${packageData ? "package-dashboard" : ""}`}>
      <header className="planner-topbar">
        <div className="planner-brand">
          <span className="planner-logo">Y</span>
          <strong>YTÜ Takvim</strong>
        </div>
        <nav className="planner-tabs" aria-label="Bölümler">
          <button className="active" type="button">Takvim</button>
        </nav>
        <div className="topbar-actions">
          <button className="gold-button" onClick={onChangePrograms} type="button">Bölümlerimi değiştir</button>
          <select
            aria-label="Aktif dönem"
            onChange={(event) => setActiveTerm(event.target.value ? Number(event.target.value) : null)}
            value={activeTerm ?? ""}
          >
            {terms.map((term) => (
              <option key={term.id} value={term.id}>
                {term.academic_year} {term.season === "fall" ? "Güz" : "Bahar"}
              </option>
            ))}
          </select>
          <span className={`api-state ${online ? "online" : ""}`}>
            <i /> {packageData ? "Yerel planlama" : online ? "Bağlı" : "Çevrimdışı"}
          </span>
        </div>
      </header>

      {error && (
        <button className="toast" onClick={() => setError(null)} type="button">
          {error}<span>×</span>
        </button>
      )}

      <div className="planner-body">
        <aside className="course-drawer" aria-label="Ders ekle">
          <div className="drawer-title">
            <div>
              <span>Ders Ekle</span>
              <small>{offerings.length} açılan ders</small>
            </div>
          </div>
          <div className="course-search">
            <span>⌕</span>
            <input
              ref={courseSearch}
              aria-label="Ders kodu veya adı ara"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Ders kodu veya adı ara…"
              type="search"
              value={search}
            />
          </div>
          <div className="course-filters">
            <label className="wide-filter">
              <span>Ders türü</span>
              <select onChange={(event) => setCategoryFilter(event.target.value)} value={categoryFilter}>
                <option value="all">Tümü</option>
                <option value="compulsory">Zorunlu</option>
                <option value="professional_elective">Mesleki seçmeli</option>
                <option value="social_elective">Sosyal seçmeli</option>
              </select>
            </label>
            <label className="wide-filter">
              <span>Bölüm</span>
              <select onChange={(event) => setMajorFilter(event.target.value)} value={majorFilter}>
                <option value="all">Tümü</option>
                {availableMajors.map((major) => (
                  <option key={major} value={major}>{major}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Dönem</span>
              <select onChange={(event) => setSeasonFilter(event.target.value)} value={seasonFilter}>
                <option value="all">Tümü</option>
                <option value="fall">Güz</option>
                <option value="spring">Bahar</option>
                {packageData && <option value="summer">Yaz</option>}
              </select>
            </label>
            <label>
              <span>Sınıf</span>
              <select onChange={(event) => setYearFilter(event.target.value)} value={yearFilter}>
                <option value="all">Tümü</option>
                {(packageData ? [...new Set([1,2,3,4,...offerings.flatMap(o => (o.curriculum_roles ?? []).flatMap(r => r.year === null ? [] : [r.year]))])].sort((a,b) => a-b) : [1,2,3,4]).map(year => <option key={year} value={year}>{year}. sınıf</option>)}
              </select>
            </label>
          </div>
          <div className="course-results">
            {filteredOfferings.map((offering) => {
              const selected = selectedIds.has(offering.id);
              return (
                <button
                  className={`search-course ${selected ? "selected" : ""}`}
                  key={offering.course_id}
                  aria-pressed={selected}
                  onClick={() => toggleCourse(offering.id)}
                  type="button"
                >
                  <span className="course-add-icon">{selected ? "✓" : "+"}</span>
                  <span className="search-course-copy">
                    <b>{offering.course_code}</b>
                    <strong>{offering.course_name}</strong>
                    <small>
                      {offering.options.length} grup · {offering.majors.join(" + ") || "Bologna eşleşmesi yok"}
                    </small>
                    <span className="group-pills">
                      {offering.options.slice(0, 6).map((option) => (
                        <i key={option.id} title={[option.instructor, option.language, option.time_status === "tba" ? "Saat belirsiz" : option.meetings.map(m => `${WEEKDAYS[m.weekday]} ${shortTime(m.starts_at)}–${shortTime(m.ends_at)} ${m.room ?? ""}`).join("; ")].filter(Boolean).join(" · ")}>{option.group_label}{option.time_status === "tba" ? " · ?" : ""}</i>
                      ))}
                    </span>
                  </span>
                </button>
              );
            })}
            {!filteredOfferings.length && (
              <div className="drawer-empty">Bu dönem için eşleşen açılmış ders yok.</div>
            )}
          </div>
        </aside>

        <main className="calendar-main">
          <div className="calendar-toolbar">
            <div>
              <h1>Haftalık Program</h1>
              <p>
                {selectedIds.size
                  ? `${selectedIds.size} ders · gruplar otomatik olarak çakışmasız seçiliyor`
                  : "Soldaki panelden ders ekleyerek başla"}
              </p>
            </div>
            <div className="solver-state" role="status">
              {scheduleBusy ? <span className="spinner" /> : <span>◇</span>}
              {scheduleBusy
                ? "Program hesaplanıyor"
                : currentSchedule
                  ? `${results?.explored_solutions ?? 0} alternatif bulundu`
                  : "Canlı çakışma kontrolü"}
            </div>
          </div>
          {packageData && <p className="dashboard-coverage">Kapsam henüz doğrulanmadı; yalnızca doğrulanabilen dersler gösterilir. Tür ve sınıf bilgisi eksik dersler “Tümü” altında gösterilir.</p>}
          <p className="dashboard-coverage" role="status" aria-atomic="true">{results?.message ?? ""}</p>
          {packageData && scheduleBusy && <button className="gold-button" onClick={() => { solverController.current?.abort(); setScheduleBusy(false); setResults({ ...dashboardResults(packageData, { status: "cancelled", alternatives: [], found: 0, visited: 0, reasons: [] }) }); }}>Aramayı iptal et</button>}
          <WeeklyCalendar schedule={currentSchedule?.selections ?? []} />
        </main>

        <aside className="program-panel" aria-label="Programım">
          <div className="program-title">
            <div>
              <h2>Programım</h2>
              <span>Varsayılan</span>
            </div>
            <small>
              {selectedIds.size
                ? `${selectedIds.size} ders · ${currentSchedule?.metrics.days_on_campus ?? "–"} ${packageData?.purpose === "weekly_preview" ? "haftalık ders günü" : packageData ? "dönem ders günü" : "kampüs günü"}`
                : "Henüz ders yok"}
            </small>
          </div>
          {packageData && <details className="dashboard-controls">
            <summary>Planlama tercihleri</summary>
            <label>Sıralama<select value={preference} onChange={e => { invalidate(); setPreference(e.target.value as Preference); }}>
              <option value="compact">Ders arası boşluk az olsun</option><option value="few_days">Ders günü az olsun</option><option value="late_start">Geç başlasın</option><option value="early_finish">Erken bitsin</option>
            </select></label>
            <fieldset><legend>Ders istemediğim saatler</legend>
              <label>Gün<select value={blockDay} onChange={e => setBlockDay(Number(e.target.value))}>{WEEKDAYS.slice(1).map((d,i) => <option key={d} value={i+1}>{d}</option>)}</select></label>
              <label>Başlangıç<input type="time" value={blockStart} onChange={e => setBlockStart(e.target.value)} /></label>
              <label>Bitiş<input type="time" value={blockEnd} onChange={e => setBlockEnd(e.target.value)} /></label>
              <button className="gold-button" disabled={!blockStart || !blockEnd || blockStart >= blockEnd || blocked.length >= 50} onClick={() => { invalidate(); setBlocked([...blocked, { weekday: blockDay, starts_at: blockStart, ends_at: blockEnd }]); }}>Aralık ekle</button>
              {blocked.map((b,i) => <p key={i}>{WEEKDAYS[b.weekday]} {b.starts_at}–{b.ends_at} <button aria-label={`${WEEKDAYS[b.weekday]} ${b.starts_at} aralığını kaldır`} onClick={() => { invalidate(); setBlocked(blocked.filter((_,j) => j !== i)); }}>Kaldır</button></p>)}
            </fieldset>
          </details>}
          <div className="selected-courses">
            {selectedOfferings.map((offering) => {
              const selection = selectedByCourse.get(offering.course_id);
              const conflict = results?.conflicts.find(
                (item) =>
                  item.left_course_id === offering.course_id ||
                  item.right_course_id === offering.course_id,
              );
              const conflictingCode =
                conflict?.left_course_id === offering.course_id
                  ? conflict.right_course_code
                  : conflict?.left_course_code;
              return (
                <article className={`selected-course ${conflict ? "has-conflict" : ""}`} key={offering.course_id}>
                  <span className={`course-swatch ${courseColor(offering.course_code)}`} />
                  <div>
                    <b>{offering.course_code}</b>
                    <strong>{offering.course_name}</strong>
                    <small>
                      {selection
                        ? `${selection.option_label} · ${selection.meetings.map((meeting) => `${WEEKDAYS[meeting.weekday]} ${shortTime(meeting.starts_at)}`).join(", ") || "TBA"}`
                        : scheduleBusy
                          ? "Grup seçiliyor…"
                          : conflict
                            ? `${conflictingCode} ile çakışıyor`
                          : "Uygun grup bulunamadı"}
                    </small>
                    {packageData && <label className="dashboard-group">Grup<select aria-label={`${offering.course_code} grup seçimi`} value={locks[offering.course_id] ?? ""} onChange={e => { invalidate(); setLocks({ ...locks, [offering.course_id]: e.target.value }); }}>
                      <option value="">Otomatik seç</option>{offering.options.map(o => <option key={o.id} value={o.id}>{o.group_label}{o.time_status === "tba" ? " — saat belirsiz" : ""}</option>)}
                    </select></label>}
                  </div>
                  <button
                    aria-label={`${offering.course_code} dersini kaldır`}
                    onClick={() => { toggleCourse(offering.id); courseSearch.current?.focus(); }}
                    type="button"
                  >
                    ×
                  </button>
                </article>
              );
            })}
            {!selectedOfferings.length && (
              <div className="program-empty">
                <span>＋</span>
                <p>Henüz ders eklenmedi</p>
                <small>Ders Ekle panelinden programını oluşturmaya başla.</small>
              </div>
            )}
          </div>
          {!!results?.conflicts.length && (
            <div className="conflict-panel">
              <div className="conflict-head">
                <strong>Çakışmalar</strong>
                <span>{results.conflicts.length}</span>
              </div>
              <p>{results.message}</p>
              <div className="conflict-list">
                {results.conflicts.slice(0, 12).map((conflict, index) => (
                  <article key={`${conflict.left_course_id}-${conflict.left_option_label}-${conflict.right_course_id}-${conflict.right_option_label}-${index}`}>
                    <b>{conflict.left_course_code} {conflict.left_option_label}</b>
                    <span>×</span>
                    <b>{conflict.right_course_code} {conflict.right_option_label}</b>
                    <strong>{conflict.left_course_name} / {conflict.right_course_name}</strong>
                    <small>
                      {WEEKDAYS[conflict.weekday]} {shortTime(conflict.starts_at)}–{shortTime(conflict.ends_at)}
                    </small>
                  </article>
                ))}
              </div>
            </div>
          )}
          {packageData?.purpose === "weekly_preview" && <small>Dönem tarihleri belirlenince takvim indirme açılacak.</small>}
          {packageData && packageData.purpose !== "weekly_preview" && currentSchedule && <button className="gold-button dashboard-download" onClick={() => {
            try {
              const groups = currentSchedule.selections.map(s => packageData.groups.find(g => g.id === s.option_id)!);
              const file = calendarExport(packageData, groups);
              const url = URL.createObjectURL(new Blob([file.content], { type: "text/calendar;charset=utf-8" }));
              const link = document.createElement("a"); link.href = url; link.download = file.filename;
              document.body.appendChild(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 10000);
            } catch (e) { setError(String(e)); }
          }}>Takvimi indir (.ics)</button>}
          {!!results?.schedules.length && (
            <div className="alternatives">
              <div className="alternatives-head">
                <strong>Alternatifler</strong>
                <span>{results.schedules.length}</span>
              </div>
              <div className="alternative-list">
                {results.schedules.map((schedule, index) => (
                  <button
                    className={activeResult === index ? "active" : ""}
                    aria-pressed={activeResult === index}
                    aria-label={`${schedule.rank}. alternatif, ${schedule.metrics.days_on_campus} ${packageData?.purpose === "weekly_preview" ? "haftalık ders günü" : packageData ? "dönem ders günü" : "gün"}, ${schedule.metrics.total_gap_minutes} dakika boşluk`}
                    key={schedule.rank}
                    onClick={() => setActiveResult(index)}
                    type="button"
                  >
                    <b>#{schedule.rank}</b>
                    <span>{schedule.metrics.days_on_campus} {packageData?.purpose === "weekly_preview" ? "haftalık ders günü" : packageData ? "dönem ders günü" : "gün"}</span>
                    <small>{schedule.metrics.total_gap_minutes} dk boşluk</small>
                  </button>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>

    </div>
  );
}

function WeeklyCalendar({ schedule }: { schedule: ScheduleSelection[] }) {
  const events = schedule.flatMap((selection) =>
    selection.meetings
      .filter((meeting) => meeting.weekday >= 1 && meeting.weekday <= 7)
      .map((meeting) => ({ selection, meeting })),
  );
  const firstHour = Math.min(START_HOUR, ...events.map(e => Math.floor(minutes(e.meeting.starts_at) / 60)));
  const lastHour = Math.max(END_HOUR, ...events.map(e => Math.ceil(minutes(e.meeting.ends_at) / 60)));
  const days = WEEKDAYS.slice(1, events.some(e => e.meeting.weekday > 5) ? 8 : 6);
  const bodyHeight = (lastHour - firstHour) * HOUR_HEIGHT;

  return (
    <div className="calendar-scroll" tabIndex={0} role="region" aria-label="Haftalık ders takvimi">
      <div className="week-calendar">
        <div className="week-head" style={{ gridTemplateColumns: `58px repeat(${days.length}, minmax(0, 1fr))` }}>
          <span />
          {days.map(day => <strong key={day}>{day}</strong>)}
        </div>
        <div className="week-body" style={{ height: bodyHeight }}>
          <div className="time-labels">
            {Array.from({ length: lastHour - firstHour + 1 }, (_, index) => (
              <span key={index} style={{ top: index * HOUR_HEIGHT - 7 }}>
                {String(firstHour + index).padStart(2, "0")}:00
              </span>
            ))}
          </div>
          <div className="day-grid" style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }}>
            {days.map((day, index) => (
              <div className="calendar-day" key={day}>
                {Array.from({ length: lastHour - firstHour }, (_, row) => (
                  <i key={row} style={{ top: row * HOUR_HEIGHT }} />
                ))}
                {events
                  .filter(({ meeting }) => meeting.weekday === index + 1)
                  .map(({ selection, meeting }) => {
                    const start = minutes(meeting.starts_at);
                    const end = minutes(meeting.ends_at);
                    const top = ((start - firstHour * 60) / 60) * HOUR_HEIGHT + 2;
                    const height = Math.max(((end - start) / 60) * HOUR_HEIGHT - 4, 30);
                    return (
                      <article
                        className={`calendar-event ${courseColor(selection.course_code)}`}
                        aria-label={`${selection.course_code} ${selection.course_name}, ${day}, ${shortTime(meeting.starts_at)}–${shortTime(meeting.ends_at)}, ${selection.option_label}${meeting.room ? `, ${meeting.room}` : ""}`}
                        key={`${selection.course_id}-${selection.option_id}-${meeting.weekday}-${meeting.starts_at}-${meeting.effective_starts_on}`}
                        style={{ top, height }}
                        title={`${selection.course_code} · ${selection.course_name} · ${selection.option_label} · ${meeting.effective_starts_on ?? ""}–${meeting.effective_ends_on ?? ""}`}
                      >
                        <b>{selection.course_code}</b>
                        <strong>{selection.course_name}</strong>
                        <span>{selection.option_label}</span>
                        <small>{shortTime(meeting.starts_at)}–{shortTime(meeting.ends_at)}</small>
                        {meeting.room && <em>{meeting.room}</em>}
                        {meeting.effective_starts_on && <small>{meeting.effective_starts_on}–{meeting.effective_ends_on}</small>}
                      </article>
                    );
                  })}
              </div>
            ))}
          </div>
          {!events.length && (
            <div className="calendar-empty">
              <span>◇</span>
              <strong>Programın boş</strong>
              <p>Takvime başlamak için soldan bir ders ekle.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
