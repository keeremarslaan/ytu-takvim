"use client";

import { useEffect, useRef, useState } from "react";
import { solveSchedules, type BlockedTime, type Preference, type SolverResult } from "../lib/browser-solver";
import { offeredCourses, type StudentPackage } from "../lib/student-package";
import { calendarExport } from "../lib/calendar-export";
const DAYS = ["", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];

export function ScheduleControls({ data, programIds, courseIds }: { data: StudentPackage; programIds: number[]; courseIds: number[] }) {
  const courses = offeredCourses(data, programIds).filter(c => courseIds.includes(c.catalog_course_id));
  const [locks, setLocks] = useState<Record<number, string>>({});
  const [blocked, setBlocked] = useState<BlockedTime[]>([]);
  const [day, setDay] = useState(1);
  const [start, setStart] = useState("09:00");
  const [end, setEnd] = useState("10:00");
  const [preference, setPreference] = useState<Preference>("compact");
  const [state, setState] = useState<{ key: string; busy?: boolean; result?: SolverResult; error?: string } | null>(null);
  const [active, setActive] = useState(0);
  const [downloadError, setDownloadError] = useState<{ key: string; message: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const request = { programIds, courseIds, locks: Object.fromEntries(Object.entries(locks).filter(([cid, group]) => courseIds.includes(Number(cid)) && group)), blocked, preference };
  const key = JSON.stringify([data.export_id, request]);
  useEffect(() => () => controller.current?.abort(), [key]);
  const current = state?.key === key ? state : null;
  const result = current?.result;
  const alternative = result?.alternatives[active];
  async function run() {
    controller.current?.abort();
    const aborter = new AbortController();
    controller.current = aborter;
    setState({ key, busy: true }); setActive(0);
    try {
      const result = await solveSchedules(data, request, { signal: aborter.signal });
      if (!aborter.signal.aborted) setState({ key, result });
    } catch (e) {
      if (!aborter.signal.aborted) setState({ key, error: e instanceof Error ? e.message : "Program oluşturulamadı." });
    }
  }
  return <section className="schedule-controls" aria-label="Program oluşturma">
    <h2>Program oluştur</h2>
    <p>Saatleri belirsiz gruplar programa alınmaz. Tüm ders saatleri, çevrim içi dersler dahil, çakışma kontrolüne dahildir.</p>
    {courses.map(c => <label className="solver-field" key={c.catalog_course_id}>{c.code} için grup
      <select value={locks[c.catalog_course_id] ?? ""} onChange={e => setLocks({ ...locks, [c.catalog_course_id]: e.target.value })}>
        <option value="">Otomatik seç</option>
        {c.groups.map(g => <option key={g.id} value={g.id}>{g.label}{g.time_status === "tba" ? " — saat belirsiz" : ""}</option>)}
      </select>
    </label>)}
    <fieldset><legend>Ders istemediğim saatler</legend>
      <p>Eklenen aralıklar dönem boyunca her hafta geçerlidir.</p>
      <div className="blocked-editor">
        <label>Gün<select value={day} onChange={e => setDay(Number(e.target.value))}>{DAYS.slice(1).map((d, i) => <option key={d} value={i + 1}>{d}</option>)}</select></label>
        <label>Başlangıç<input type="time" required value={start} onChange={e => setStart(e.target.value)} /></label>
        <label>Bitiş<input type="time" required value={end} onChange={e => setEnd(e.target.value)} /></label>
        <button type="button" className="gold-button" disabled={!start || !end || start >= end || blocked.length >= 50} onClick={() => setBlocked([...blocked, { weekday: day, starts_at: start, ends_at: end }])}>Aralık ekle</button>
      </div>
      {(!start || !end || start >= end) && <p role="alert">Bitiş saati başlangıçtan sonra olmalıdır.</p>}
      <ul>{blocked.map((b, i) => <li key={i}>{DAYS[b.weekday]} {b.starts_at}–{b.ends_at} <button type="button" onClick={() => setBlocked(blocked.filter((_, j) => j !== i))} aria-label={`${DAYS[b.weekday]} ${b.starts_at} aralığını kaldır`}>Kaldır</button></li>)}</ul>
    </fieldset>
    <label className="solver-field">Sıralama tercihi<select value={preference} onChange={e => setPreference(e.target.value as Preference)}>
      <option value="compact">Ders arası boşluk az olsun</option><option value="few_days">Ders olan gün sayısı az olsun</option><option value="late_start">10:00 öncesi ders az olsun</option><option value="early_finish">17:00 sonrası ders az olsun</option>
    </select></label>
    <button className="gold-button" type="button" disabled={!courseIds.length || current?.busy} onClick={run}>Programları bul</button>
    {current?.busy && <button className="gold-button" type="button" onClick={() => { controller.current?.abort(); setState({ key, result: { status: "cancelled", alternatives: [], visited: 0, found: 0, reasons: [] } }); }}>Aramayı iptal et</button>}
    <div role="status" aria-live="polite">
      {current?.busy && <p>Programlar aranıyor…</p>}
      {current?.error && <p>{current.error}</p>}
      {result?.status === "cancelled" && <p>Arama iptal edildi.</p>}
      {result?.status === "limited" && <p>Arama sınırına ulaşıldı. {result.alternatives.length ? "Bulunan uygun programlar gösteriliyor; en iyi seçenek garanti değildir." : "Henüz uygun program bulunamadı; bu, çözüm olmadığı anlamına gelmez."}</p>}
      {result?.status === "infeasible" && <p>Bu seçimlerle saatleri doğrulanmış uygun program yok. Dersleri, grup kilitlerini veya engellenen saatleri gözden geçirin.</p>}
      {result?.status === "complete" && <p>Arama tamamlandı. {result.found} uygun program bulundu; en iyi {result.alternatives.length} gösteriliyor.</p>}
      {result?.reasons.map(r => <p key={r}>{r}</p>)}
    </div>
    {!!result?.alternatives.length && <>
      <label className="solver-field">Program alternatifi<select value={active} onChange={e => setActive(Number(e.target.value))}>{result.alternatives.map((a, i) => <option key={a.key} value={i}>Program {i + 1}</option>)}</select></label>
      {alternative && <div className="schedule-result">
        <h3>Program {active + 1}</h3>
        <button type="button" className="gold-button" onClick={() => {
          try {
            const file = calendarExport(data, alternative.groups);
            const url = URL.createObjectURL(new Blob([file.content], { type: "text/calendar;charset=utf-8" }));
            const link = document.createElement("a");
            link.href = url; link.download = file.filename;
            document.body.appendChild(link); link.click(); link.remove();
            window.setTimeout(() => URL.revokeObjectURL(url), 10000);
            setDownloadError(null);
          } catch (error) {
            setDownloadError({ key: key + alternative.key, message: error instanceof Error ? error.message : "Takvim indirilemedi." });
          }
        }}>Takvimi indir (.ics)</button>
        <p>Seçili programın tüm derslerini takvim uygulamanıza aktarabilirsiniz. Saatler İstanbul saatine göre hazırlanır; takviminizde seçtiğiniz saat diliminde gösterilir.</p>
        {downloadError?.key === key + alternative.key && <p role="alert">{downloadError.message}</p>}
        <p>Dönem toplamı: {alternative.metrics.teachingDays} ders günü · {Math.round(alternative.metrics.gapMinutes)} dakika ders arası boşluk</p>
        <p>Ders günü sayısı çevrim içi dersleri de içerir; kampüse gidilen gün sayısı değildir.</p>
        <div className="schedule-week">{DAYS.slice(1).map((d, i) => {
          const meetings = alternative.groups.flatMap(g => g.meetings.filter(m => m.weekday === i + 1).map(m => ({ g, m }))).sort((a, b) => a.m.starts_at.localeCompare(b.m.starts_at));
          return <section key={d}><h4>{d}</h4>{meetings.length ? meetings.map(({ g, m }, j) => <article key={`${g.id}-${j}`}>
            <strong>{courses.find(c => c.catalog_course_id === g.catalog_course_id)?.code} · {g.label}</strong>
            <p>{m.starts_at.slice(0, 5)}–{m.ends_at.slice(0, 5)}{m.room && ` · ${m.room}`}</p>
            <small>{m.effective_starts_on}–{m.effective_ends_on}</small>
          </article>) : <p>Ders yok</p>}</section>;
        })}</div>
      </div>}
    </>}
  </section>;
}
