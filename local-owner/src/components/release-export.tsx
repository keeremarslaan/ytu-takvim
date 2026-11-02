"use client";

import { useEffect, useState } from "react";
import styles from "./owner-review.module.css";

type Draft = { id: string; publisher: string; source_key: string; revision: string; term_code: string };
type ExportRecord = { id: string; created_at: string; term_code: string; purpose: string };
type History = { items: ExportRecord[]; heads: { term_code: string; purpose: string; export_id: string }[]; next_offset: number | null };
type Report = {
  term: { starts_on: string | null; ends_on: string | null; timezone: string } | null;
  source_changes: { publisher: string; key: string; before_revision: string | null; after_revision: string; action: string }[];
  ready_to_export: boolean; blockers: string[]; warnings: string[];
  summary: { sources: number; courses: number; groups: number; withdrawals: number };
  coverage: { bologna_program_id: number; name: string; status: string; issues: string[]; exported_eligible_courses: number }[];
  documents: { draft_id: string; document: { source: { publisher: string; key: string; revision: string }; withdraw_all: boolean }; review: { summary: { added: number; changed: number; removed: number; unchanged: number } } }[];
};
const API = "http://127.0.0.1:8001/owner/exports";
async function request(path = "", body?: object) {
  const response = await fetch(`${API}${path}`, { method: body ? "POST" : "GET", cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail));
  return value;
}
function Downloads({ id }: { id: string }) {
  return <p><a href={`${API}/${id}/student-data`}>Öğrenci verisini indir</a>{" · "}
    <a href={`${API}/${id}/owner-report`}>Sahip inceleme raporunu indir</a></p>;
}
export function ReleaseExport({ drafts }: { drafts: Draft[] }) {
  const [term, setTerm] = useState("");
  const [purpose, setPurpose] = useState("test");
  const [selected, setSelected] = useState<string[]>([]);
  const [baseline, setBaseline] = useState("");
  const [history, setHistory] = useState<History>({ items: [], heads: [], next_offset: null });
  const [preview, setPreview] = useState<{ digest: string; report: Report } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [exported, setExported] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    request().then((result: History) => { if (!cancelled) setHistory(result); })
      .catch((reason) => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
  }, []);
  function invalidate() { setPreview(null); setConfirmed(false); setExported(null); setError(""); }
  const selection = { term_code: term, purpose, draft_ids: selected, baseline_export_id: baseline || null };
  const head = history.heads.find((h) => h.term_code === term && h.purpose === purpose);
  async function check() {
    setBusy(true); invalidate();
    try { setPreview(await request("/preview", selection)); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!preview || !confirmed) return;
    setBusy(true); setError("");
    try {
      const result = await request("", { ...selection, expected_digest: preview.digest, owner_confirmed: true });
      setExported(result.id); setPreview(null); setConfirmed(false);
      setBaseline(result.id);
      setHistory(await request());
    } catch (reason) { setError(String(reason)); setPreview(null); setConfirmed(false); }
    finally { setBusy(false); }
  }
  async function loadHistory(more = false) {
    setBusy(true); setError("");
    try {
      const result: History = await request(more ? `?offset=${history.next_offset}` : "");
      setHistory((current) => ({ ...result, items: more ? [...current.items, ...result.items] : result.items }));
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }
  return <section className={styles.panel} aria-label="Veri paketi hazırlama">
    <h2>Veri paketi hazırla</h2>
    <p>Bir dönemin tüm kaynaklarını tek pakette seçin. Önceki paketin kaynakları korunmalı, yeni sürümle değiştirilmeli veya açıkça geri çekilmelidir. Paket oluşturmak siteyi yayımlamaz.</p>
    <fieldset disabled={busy}>
      <label>Dönem<select value={term} onChange={(e) => { setTerm(e.target.value); setSelected([]); setBaseline(""); invalidate(); }}>
        <option value="">Dönem seçin</option>
        {[...new Set(drafts.map((d) => d.term_code))].sort().map((t) => <option key={t}>{t}</option>)}
      </select></label>
      <label>Paket amacı<select value={purpose} onChange={(e) => { setPurpose(e.target.value); setBaseline(""); invalidate(); }}>
        <option value="test">Deneme — yayımlanmaz</option><option value="release">Gerçek ders verisi</option>
      </select></label>
      <label>Önceki yerel paket<select value={baseline} onChange={(e) => { setBaseline(e.target.value); invalidate(); }}>
        <option value="">İlk paket / önceki paket yok</option>
        {head && <option value={head.export_id}>Son yerel paket · {head.export_id.slice(0, 12)}</option>}
      </select></label>
      <p>Bu seçim son yerel dışa aktarımı gösterir; sitede hangi sürümün yayımlandığını göstermez.</p>
      <h3>Dahil edilecek taslaklar</h3>
      <p>Hiçbir taslak otomatik seçilmez. Listede eksik kayıt varsa yukarıdaki “Daha fazla taslak” düğmesini kullanın.</p>
      {drafts.filter((d) => d.term_code === term).map((d) => <label className={styles.check} key={d.id}>
        <input type="checkbox" checked={selected.includes(d.id)} onChange={(e) => {
          setSelected((ids) => e.target.checked ? [...ids, d.id] : ids.filter((id) => id !== d.id)); invalidate();
        }} />
        <span>{d.publisher} · {d.source_key} · {d.revision} · {d.id.slice(0, 8)}</span>
      </label>)}
      <p>{selected.length} taslak seçildi.</p>
      <button disabled={!term || !selected.length || busy} onClick={check}>Seçimi kontrol et</button>
    </fieldset>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    <div aria-live="polite">{busy && <p>Kontrol ediliyor…</p>}
    {preview && <>
      <h3>{preview.report.ready_to_export ? "Paket incelemeye hazır" : "Paket için düzeltme gerekiyor"}</h3>
      <p>{preview.report.summary.sources} kaynak · {preview.report.summary.courses} ders · {preview.report.summary.groups} grup · {preview.report.summary.withdrawals} geri çekme</p>
      {preview.report.term && <p>Öğretim tarihleri: {preview.report.term.starts_on ?? "Eksik"} – {preview.report.term.ends_on ?? "Eksik"} · {preview.report.term.timezone}</p>}
      {preview.report.source_changes.map((s, i) => <p key={`${s.key}-${i}`}>
        {s.publisher} · {s.key}: {s.before_revision ?? "İlk sürüm"} → {s.after_revision} · {{ added: "Yeni kaynak", retained: "Korunuyor", replaced: "Değiştiriliyor", withdrawn: "Geri çekiliyor" }[s.action] ?? s.action}
      </p>)}
      {preview.report.blockers.length > 0 && <ul className={styles.error}>{preview.report.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
      <ul>{preview.report.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
      {preview.report.documents.map((d) => <p key={d.draft_id}>
        {d.document.source.publisher} · {d.document.source.key} · {d.document.source.revision}{d.document.withdraw_all && " · GERİ ÇEKME"}<br />
        Taslak karşılaştırması: {d.review.summary.added} yeni · {d.review.summary.changed} değişen · {d.review.summary.removed} kaldırılan
      </p>)}
      <details><summary>Bölümlere göre kapsam ve eksikler</summary>
        {preview.report.coverage.map((p) => <p key={p.bologna_program_id}><strong>{p.name}</strong><br />
          {p.exported_eligible_courses} uygun ders pakette · Müfredat: {p.status}{p.issues.length > 0 && ` · ${p.issues.join("; ")}`}
        </p>)}
        <p>Ders programlarının tamlığı henüz doğrulanmadı. Sıfır ders, bölümde ders açılmadığı anlamına gelmez.</p>
      </details>
      <details><summary>Tam paket inceleme raporu</summary><pre>{JSON.stringify(preview.report, null, 2)}</pre></details>
      {preview.report.ready_to_export && <>
        <label className={styles.check}><input type="checkbox" disabled={busy} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          <span>{purpose === "release" ? "Seçili kaynakları resmî belgelerle karşılaştırdım; sürümleri, kısıtları ve kapsam eksiklerini inceledim." : "Bu paketin yalnızca deneme verisi olduğunu ve yayımlanmayacağını anlıyorum."}</span>
        </label>
        <button disabled={!confirmed || busy} onClick={save}>Yeniden doğrula ve paketi oluştur</button>
      </>}
    </>}
    {exported && <><p>Paket yerel olarak kaydedildi. Öğrenci sitesi değiştirilmedi.</p><Downloads id={exported} /></>}
    </div>
    <h3>Kaydedilmiş paketler</h3>
    <button disabled={busy} onClick={() => loadHistory()}>Paket listesini yenile</button>
    {history.items.map((item) => <article className={styles.group} key={item.id}>
      <strong>{item.term_code} · {item.purpose === "test" ? "DENEME" : "Gerçek ders verisi"}</strong>
      <p>{new Date(item.created_at).toLocaleString("tr-TR")} · {item.id.slice(0, 12)}</p><Downloads id={item.id} />
    </article>)}
    {history.next_offset !== null && <button disabled={busy} onClick={() => loadHistory(true)}>Daha fazla paket</button>}
  </section>;
}
