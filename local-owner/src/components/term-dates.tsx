"use client";

import { useEffect, useState } from "react";
import styles from "./owner-review.module.css";

type Term = { code: string; starts_on: string | null; ends_on: string | null; timezone: string };
const API = "http://127.0.0.1:8001/owner/terms";
async function request(path = "", body?: object) {
  const response = await fetch(API + path, { cache: "no-store", method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Dönem kaydedilemedi. Akademik yılı ve tarihleri kontrol edin.");
  return value;
}
export function TermDates({ onSaved }: { onSaved: () => void }) {
  const [terms, setTerms] = useState<Term[]>([]);
  const [code, setCode] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [review, setReview] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [creating, setCreating] = useState(false);
  const [year, setYear] = useState("");
  const [season, setSeason] = useState("fall");
  useEffect(() => {
    let cancelled = false;
    request().then((items: Term[]) => { if (!cancelled) setTerms(items); })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, []);
  const term = terms.find((t) => t.code === code);
  const firstYear = Number(year);
  const validYear = /^[0-9]{4}$/.test(year) && firstYear >= 1 && firstYear < 9999;
  const academicYear = validYear ? `${year}-${String(firstYear + 1).padStart(4, "0")}` : "";
  const newCode = academicYear ? `${academicYear}-${season}` : "";
  const duplicate = terms.some((t) => t.code === newCode);
  const days = (Date.parse(end) - Date.parse(start)) / 86400000;
  const valid = !!term && term.timezone === "Europe/Istanbul" && !!start && !!end && days >= 0 && days <= 370;
  const changed = start !== (term?.starts_on ?? "") || end !== (term?.ends_on ?? "");
  function resetReview() { setReview(false); setConfirmed(false); setError(""); setMessage(""); }
  async function refresh() {
    setBusy(true); resetReview();
    try { setTerms(await request()); setCode(""); setStart(""); setEnd(""); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!term || !valid || !review || !confirmed) return;
    setBusy(true); setError("");
    try {
      const saved: Term = await request(`/${encodeURIComponent(code)}/dates`, {
        starts_on: start, ends_on: end, expected_starts_on: term.starts_on,
        expected_ends_on: term.ends_on, owner_confirmed: true,
      });
      setTerms((items) => items.map((t) => t.code === saved.code ? saved : t));
      setMessage("Öğretim tarihleri yerel olarak kaydedildi. Mevcut paketler değişmedi; yeni paket için seçimi yeniden kontrol edin.");
      onSaved();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); setReview(false); setConfirmed(false); }
  }
  async function create() {
    if (!validYear || duplicate || !review || !confirmed) return;
    setBusy(true); setError("");
    try {
      const saved: Term = await request("", { academic_year: academicYear, season, owner_confirmed: true });
      setTerms((items) => [...items, saved].sort((a, b) => a.code.localeCompare(b.code)));
      setCode(saved.code); setStart(""); setEnd(""); setCreating(false);
      setMessage("Dönem oluşturuldu. Öğretim tarihlerini resmî akademik takvim açıklandığında aşağıdan girebilirsiniz.");
      onSaved();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); setReview(false); setConfirmed(false); }
  }
  return <section className={styles.panel} aria-label="Öğretim tarihleri">
    <h2>Öğretim tarihleri</h2>
    <p>Resmî akademik takvimdeki ilk ve son ders tarihini girin. Bu tarihler takvime aktarılacak derslerin sınırlarını belirler. Saat dilimi: Europe/Istanbul.</p>
    <fieldset disabled={busy}>
      <button onClick={refresh}>Dönem listesini yenile</button>
      <button onClick={() => { setCreating(!creating); resetReview(); }}>{creating ? "Yeni dönemden vazgeç" : "Yeni dönem ekle"}</button>
      {creating ? <>
        <h3>Yeni dönem</h3>
        <label>Akademik yılın başlangıç yılı<input type="number" min="1" max="9998" placeholder="Örn. 2027" value={year} onChange={(e) => { setYear(e.target.value); resetReview(); }} /></label>
        <label>Yarıyıl<select value={season} onChange={(e) => { setSeason(e.target.value); resetReview(); }}>
          <option value="fall">Güz</option><option value="spring">Bahar</option><option value="summer">Yaz</option>
        </select></label>
        {validYear && <p>Akademik yıl: {academicYear} · Dönem: {newCode}</p>}
        {duplicate && <p>Bu dönem zaten var. Yeni dönemden vazgeçip listeden seçerek tarihlerini düzenleyebilirsiniz.</p>}
        <p>Öğretim tarihlerini daha sonra girebilirsiniz. Tarihler tamamlanmadan veri paketi oluşturulamaz.</p>
        <button disabled={!validYear || duplicate} onClick={() => { setReview(true); setConfirmed(false); }}>Yeni dönemi incele</button>
        {review && <>
          <h3>Oluşturulacak dönem</h3>
          <p>{academicYear} · {{ fall: "Güz", spring: "Bahar", summer: "Yaz" }[season]} · Öğretim tarihleri: henüz girilmedi</p>
          <label className={styles.check}><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /><span>Akademik yılı ve yarıyılı kontrol ettim.</span></label>
          <button disabled={!confirmed || !validYear || duplicate} onClick={create}>Dönemi oluştur</button>
        </>}
      </> : <>
      <label>Tarihleri düzenlenecek dönem<select value={code} onChange={(e) => {
        const selected = terms.find((t) => t.code === e.target.value);
        setCode(e.target.value); setStart(selected?.starts_on ?? ""); setEnd(selected?.ends_on ?? ""); resetReview();
      }}><option value="">Dönem seçin</option>{terms.map((t) => <option key={t.code}>{t.code}</option>)}</select></label>
      {term && <>
        <p>Kayıtlı: {term.starts_on ?? "Eksik"} – {term.ends_on ?? "Eksik"}</p>
        <label>İlk ders tarihi<input type="date" required value={start} onChange={(e) => { setStart(e.target.value); resetReview(); }} /></label>
        <label>Son ders tarihi<input type="date" required min={start || undefined} value={end} onChange={(e) => { setEnd(e.target.value); resetReview(); }} /></label>
        {!valid && <p>İki tarih de gerekli. Son tarih ilk tarihten önce olamaz; aralık en fazla 370 gündür. Saat dilimi Europe/Istanbul olmalıdır.</p>}
        <button disabled={!valid || !changed} onClick={() => { setReview(true); setConfirmed(false); }}>Tarih değişikliğini incele</button>
        {review && <>
          <h3>Kaydedilecek değişiklik</h3>
          <p>{term.starts_on ?? "Eksik"} – {term.ends_on ?? "Eksik"} → {start} – {end}</p>
          <p>Mevcut taslaklar ve indirilmiş paketler değişmez. Yeni paket hazırlanırken derslerin bu aralığa uygunluğu yeniden kontrol edilir.</p>
          <label className={styles.check}><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /><span>Bu öğretim tarihlerini resmî akademik takvimle karşılaştırdım.</span></label>
          <button disabled={!confirmed || !valid} onClick={save}>Öğretim tarihlerini kaydet</button>
        </>}
      </>}
      </>}
    </fieldset>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    <p role="status">{busy ? "Yükleniyor…" : message}</p>
  </section>;
}
