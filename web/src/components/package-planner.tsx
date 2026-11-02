"use client";

import { ScheduleControls } from "./schedule-controls";
import { useEffect, useRef, useState } from "react";
import { offeredCourses, type StudentPackage } from "../lib/student-package";

const MODES: Record<string, string> = { in_person: "Yüz yüze", online: "Çevrim içi", hybrid: "Hibrit", unknown: "Ders biçimi belirtilmemiş" };
const DAYS = ["", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];

export function PackagePlanner({ data, programIds, onChangePrograms }: { data: StudentPackage; programIds: number[]; onChangePrograms: () => void }) {
  const courses = offeredCourses(data, programIds);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const query = search.trim().toLocaleLowerCase("tr");
  function toggle(id: number) { setSelected(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  return <main className="package-planner">
    <header className="planner-topbar"><strong>YTÜ Ders Planlayıcı</strong><button type="button" className="gold-button" onClick={onChangePrograms}>Bölümlerimi değiştir</button></header>
    <h1 ref={heading} tabIndex={-1}>Dersler ve gruplar</h1>
    <p>{data.programs.filter(p => programIds.includes(p.id)).map(p => p.name).join(" + ")}</p>
    <div className="package-notice">
      <p>Ders programı kapsamı henüz doğrulanmadı. Listede olmayan dersler için bölümünüzün duyurularını kontrol edin.</p>
      {data.programs.some(p => programIds.includes(p.id) && !p.curriculum_ready) && <p>Seçtiğiniz bölümlerden en az birinin müfredat verisi eksik. Yalnızca doğrulanabilen dersler gösterilir.</p>}

    </div>
    <label className="package-search">Ders kodu veya adı <input type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>
    <p role="status">{courses.length} açılan ders · {selected.size} seçili ders</p>
    {!!selected.size && <section aria-label="Seçili dersler"><h2>Seçili dersler</h2>{courses.filter(c => selected.has(c.catalog_course_id)).map(c => <button className="gold-button" type="button" key={c.catalog_course_id} onClick={() => toggle(c.catalog_course_id)} aria-label={`${c.code} dersini kaldır`}>{c.code} · Kaldır</button>)}</section>}
    <ScheduleControls data={data} programIds={programIds} courseIds={[...selected].sort((a, b) => a - b)} />
    <div className="package-courses">{courses.filter(c => `${c.code} ${c.name}`.toLocaleLowerCase("tr").includes(query)).map(c => <article className="package-course" key={c.catalog_course_id}>
      <label><input type="checkbox" checked={selected.has(c.catalog_course_id)} onChange={() => toggle(c.catalog_course_id)} /> <strong>{c.code} · {c.name}</strong></label>
      {c.prerequisite_text && <p>Ön koşul: {c.prerequisite_text}</p>}
      <ul>{c.groups.map(g => <li key={g.id}>
        <strong>{g.label}</strong>{g.instructor && ` · ${g.instructor}`}{g.language && ` · ${g.language}`}
        <small>Uygun bölüm: {data.programs.filter(p => programIds.includes(p.id) && g.eligible_bologna_program_ids.includes(p.id)).map(p => p.name).join(" + ")}</small>
        {g.time_status === "tba" ? <p>Saat henüz belli değil — çakışmasız olduğu doğrulanamaz.</p> : g.meetings.map((m, i) => <p key={i}>{DAYS[m.weekday]} {m.starts_at.slice(0, 5)}–{m.ends_at.slice(0, 5)}{m.room && ` · ${m.room}`} · {MODES[m.delivery_mode]} · {m.effective_starts_on}–{m.effective_ends_on}</p>)}
      </li>)}</ul>
    </article>)}</div>
    {!courses.length && <p role="status">Seçtiğiniz bölümler için bu pakette uygun açılmış ders bulunmuyor. Bu, ders açılmadığı anlamına gelmez.</p>}
    {!!courses.length && !courses.some(c => `${c.code} ${c.name}`.toLocaleLowerCase("tr").includes(query)) && <p role="status">Aramanızla eşleşen ders bulunamadı.</p>}
  </main>;
}
