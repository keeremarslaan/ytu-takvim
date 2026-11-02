"use client";

import { FormEvent, useEffect, useState } from "react";
import styles from "./owner-review.module.css";
import { TermDates } from "./term-dates";
import { ReleaseExport } from "./release-export";

const API_URL = "http://127.0.0.1:8001";
const DAYS = ["", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];
type Evidence = { page?: number; sheet?: string; rows?: string; quote: string };
type Course = {
  code: string; name: string;
  groups: Array<{
    label: string; time_status: string; language: string | null; instructor: string | null;
    evidence?: Evidence;
    permissions: { allowed_bologna_program_ids: number[] | null; excluded_bologna_program_ids: number[]; note: string | null; unresolved: boolean; evidence?: Evidence };
    meetings: Array<{ weekday: number; starts_at: string; ends_at: string; room: string | null; starts_on: string | null; ends_on: string | null; delivery_mode: string; evidence?: Evidence }>;
  }>;
};
type Draft = { id: string; created_at: string; publisher: string; source_key: string; revision: string; term_code: string; baseline_id: string | null };
type DraftPage = { items: Draft[]; next_offset: number | null };
type Review = {
  draft_id?: string;

  report: {
    ready_for_owner_review: boolean; blockers: string[]; warnings: string[];
    source: { publisher: string; revision: string; filename: string };
    term: { code: string };
    summary: { added: number; removed: number; changed: number; unchanged: number };
    changes: { added: Array<{ code: string; after: Course }>; removed: Array<{ code: string; before: Course }>; changed: Array<{ code: string; before: Course; after: Course }> };
    resolved_courses: Array<{ code: string; catalog_course_id: number | null; candidates: Array<{ id: number; code: string; name: string }> }>;
  };
  document: { courses: Course[] };
};

async function ownerRequest(path: string, body?: FormData) {
  const response = await fetch(`${API_URL}/owner/${path}`, {
    method: body ? "POST" : "GET", body, cache: "no-store",
  });
  const value = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 403) throw new Error("Bu aracı yalnızca bilgisayarınızdaki yerel adresinden açın.");
    throw new Error(typeof value?.detail === "string" ? value.detail : JSON.stringify(value?.detail ?? "İstek tamamlanamadı."));
  }
  return value;
}

function CourseDetails({ course }: { course: Course }) {
  return <div className={styles.course}>
    <h4>{course.code} · {course.name}</h4>
    {course.groups.map((group) => <div key={group.label} className={styles.group}>
      <strong>{group.label}</strong> · {group.instructor ?? "Öğretim elemanı belirtilmemiş"} {group.language && `· ${group.language}`}
      {group.time_status === "tba" && <p>Saat henüz belli değil.</p>}
      {group.meetings.map((meeting, index) => <p key={index}>
        {DAYS[meeting.weekday]} {meeting.starts_at.slice(0, 5)}–{meeting.ends_at.slice(0, 5)} · {meeting.room ?? "Derslik belirtilmemiş"} · {meeting.delivery_mode}
        {meeting.starts_on && ` · ${meeting.starts_on} – ${meeting.ends_on}`}
      </p>)}
      <p>Ek bölüm kısıtı: {group.permissions.allowed_bologna_program_ids?.join(", ") ?? "Belirtilmemiş"}
        {!!group.permissions.excluded_bologna_program_ids.length && ` · Hariç: ${group.permissions.excluded_bologna_program_ids.join(", ")}`}
      </p>
      {group.permissions.note && <p>{group.permissions.note}</p>}
      {group.permissions.unresolved && <p className={styles.error}>Kayıt kısıtı çözümlenmeli.</p>}
    </div>)}
  </div>;
}

export function OwnerReview() {
  const [dateRevision, setDateRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingDrafts, setLoadingDrafts] = useState(true);

  useEffect(() => {
    let cancelled = false;
    ownerRequest("drafts").then((page: DraftPage) => {
      if (!cancelled) { setDrafts(page.items); setNextOffset(page.next_offset); }
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Taslaklar yüklenemedi.");
    }).finally(() => { if (!cancelled) setLoadingDrafts(false); });
    return () => { cancelled = true; };
  }, []);

  async function loadDrafts(offset = 0) {
    setLoadingDrafts(true); setError("");
    try {
      const page: DraftPage = await ownerRequest(`drafts?offset=${offset}`);
      setDrafts((current) => offset ? [...current, ...page.items.filter((item) => !current.some((d) => d.id === item.id))] : page.items);
      setNextOffset(page.next_offset);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Taslaklar yüklenemedi."); }
    finally { setLoadingDrafts(false); }
  }

  async function reopen(id: string) {
    setBusy(true); setError(""); setReview(null);
    try { setReview(await ownerRequest(`drafts/${id}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Taslak açılamadı."); }
    finally { setBusy(false); }
  }

  async function preview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (!form.get("baseline_id")) form.delete("baseline_id");
    for (const value of form.values()) {
      if (value instanceof File && value.size > 20 * 1024 * 1024) {
        setError("Her dosya en fazla 20 MB olabilir."); return;
      }
    }
    setBusy(true); setError(""); setReview(null);
    try { setReview(await ownerRequest("drafts", form)); await loadDrafts(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "İnceleme tamamlanamadı."); }
    finally { setBusy(false); }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(review, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "schedule-review.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <main className={styles.page}><div className={styles.container}>
    <header className={styles.header}><a href="http://localhost:3000/">← Öğrenci önizlemesi</a></header>
    <p className={styles.eyebrow}>YTÜ TAKVİM · YEREL VERİ HAZIRLAMA</p>
    <h1>Ders verilerini incele</h1>
    <p>Bu araç yalnızca bilgisayarınızda çalışır. Kaynak belgeyi ve standart ders dosyasını karşılaştırın; hataları, grupları ve değişiklikleri kontrol edin.</p>
      <form className={styles.panel} onSubmit={preview} onChange={() => { setReview(null); setError(""); }}>
        <h2>Yeni taslak</h2>
        <fieldset disabled={busy}>
          <label>Orijinal kaynak belge<input type="file" name="source" required /></label>
          <label>Standart ders dosyası (JSON v1.0)<input type="file" name="document" accept=".json,application/json" required /></label>
          <label>Karşılaştırılacak kayıtlı taslak (isteğe bağlı)
            <select name="baseline_id" defaultValue="">
              <option value="">İlk sürüm / karşılaştırma yok</option>
              {drafts.map((draft) => <option value={draft.id} key={draft.id}>{draft.publisher} · {draft.source_key} · {draft.revision} · {draft.term_code} · {draft.id.slice(0, 8)}</option>)}
            </select>
          </label>
        </fieldset>
        <p>Her dosya en fazla 20 MB. Taslaklar kaynak belgeleriyle birlikte bilgisayarınızda saklanır. Önceki taslak yalnızca karşılaştırma içindir; yayımlanmış sürüm anlamına gelmez.</p>
        <button disabled={busy}>{busy ? "İşleniyor…" : "Taslağı kaydet ve incele"}</button>
      </form>
      <p className={styles.notice}>Taslak kaydetmek öğrenci programını değiştirmez. Düzeltmeleri yeni bir taslak olarak yükleyin; önceki kayıtlar korunur. Her açılışta güncel katalogla yeniden kontrol edilir.</p>
      <section className={styles.panel} aria-label="Kayıtlı taslaklar">
        <h2>Kayıtlı taslaklar</h2>
        <button disabled={busy || loadingDrafts} onClick={() => loadDrafts()}>Listeyi yenile</button>
        {loadingDrafts && <p role="status">Taslaklar yükleniyor…</p>}
        {!loadingDrafts && !drafts.length && <p>Henüz kayıtlı taslak yok.</p>}
        {drafts.map((draft) => <article className={styles.group} key={draft.id}>
          <strong>{draft.publisher} · {draft.revision}</strong>
          <p>{draft.source_key} · {draft.term_code} · {new Date(draft.created_at).toLocaleString("tr-TR")} · {draft.id.slice(0, 8)}</p>
          <button disabled={busy} onClick={() => reopen(draft.id)}>Aç ve yeniden kontrol et</button>
        </article>)}
        {nextOffset !== null && <button disabled={busy || loadingDrafts} onClick={() => loadDrafts(nextOffset)}>Daha fazla taslak</button>}
      </section>
    <TermDates onSaved={() => setDateRevision((n) => n + 1)} />
    <ReleaseExport key={dateRevision} drafts={drafts} />
    {error && <p role="alert" className={styles.error}>{error}</p>}
    <div aria-live="polite">{review && <section className={styles.panel}>
      <h2>{review.report.ready_for_owner_review ? "İncelemeye hazır" : "Düzeltme gerekiyor"}</h2>
      <p>{review.report.source.publisher} · {review.report.source.revision} · {review.report.term.code}</p>
      {review.draft_id && <p>Taslak kaydedildi · {review.draft_id.slice(0, 8)}</p>}
      <button onClick={download}>Raporu indir</button>
      {review.draft_id && <p>
        <a href={`${API_URL}/owner/drafts/${review.draft_id}/source`}>Kaynak belgeyi indir</a>{" · "}
        <a href={`${API_URL}/owner/drafts/${review.draft_id}/document`}>Ders JSON dosyasını indir</a>
      </p>}
      {review.report.blockers.length > 0 && <><h3>İlerlemeyi engelleyen konular</h3><ul>{review.report.blockers.map((message, index) => <li key={index}>{message}</li>)}</ul></>}
      {review.report.warnings.length > 0 && <><h3>Kontrol edilmesi gerekenler</h3><ul>{review.report.warnings.map((message, index) => <li key={index}>{message}</li>)}</ul></>}
      <h3>Değişiklik özeti</h3>
      <p>{review.report.summary.added} yeni · {review.report.summary.changed} değişen · {review.report.summary.removed} kaldırılan · {review.report.summary.unchanged} aynı</p>
      {review.report.changes.removed.map((item) => <details key={`removed-${item.code}`}><summary>Kaldırılan: {item.code}</summary><CourseDetails course={item.before} /></details>)}
      {review.report.changes.changed.map((item) => <details key={`changed-${item.code}`}><summary>Değişen: {item.code}</summary><h3>Önce</h3><CourseDetails course={item.before} /><h3>Sonra</h3><CourseDetails course={item.after} /></details>)}
      <h3>Dosyadaki dersler ve kaynak kanıtları</h3>
      {review.document.courses.map((course) => <details key={course.code}>
        <summary>{course.code} · {course.name} · {course.groups.length} grup</summary>
        <CourseDetails course={course} />
        <details><summary>Kaynak alıntıları ve tüm alanlar</summary><pre>{JSON.stringify(course, null, 2)}</pre></details>
      </details>)}
      <details><summary>Katalog eşleştirmeleri</summary>
        {review.report.resolved_courses.map((course) => <p key={course.code}>{course.code}: {course.catalog_course_id ? `Seçili kayıt ${course.catalog_course_id}` : "Eşleştirme gerekli"}<br />{course.candidates.map((candidate) => `${candidate.id} · ${candidate.name}`).join(" / ")}</p>)}
      </details>
      <details><summary>Tam inceleme raporu</summary><pre>{JSON.stringify(review.report, null, 2)}</pre></details>
    </section>}</div>
  </div></main>;
}
