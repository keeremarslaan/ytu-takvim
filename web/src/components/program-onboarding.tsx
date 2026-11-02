"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import type { StudentPackage } from "../lib/student-package";
import { Dashboard } from "@/components/dashboard";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const PROFILE_KEY = "ytu-scheduler.programs.v1";
type Program = { id: number; name: string; curriculum_ready: boolean; curriculum_available: boolean };

function MajorField({ label, programs, value, onChange }: {
  label: string;
  programs: Program[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="major-field">
      <legend>{label}</legend>
      <select aria-label={label} required value={value}
        onChange={(event) => onChange(event.target.value)}>
        <option value="">Bölüm seçin</option>
        {programs.map((program) => <option key={program.id} value={program.id} disabled={!program.curriculum_available}>
          {program.name}{program.curriculum_available ? "" : " — Müfredat verisi hazırlanıyor"}
        </option>)}
      </select>
      {programs.some((p) => String(p.id) === value && p.curriculum_available && !p.curriculum_ready) &&
        <small>Bu bölümün Bologna sayfasında bazı seçmeli ders listeleri eksik. Yalnızca doğrulanabilen dersler gösterilir.</small>}
    </fieldset>
  );
}

export function ProgramOnboarding({ packageData }: { packageData?: StudentPackage }) {
  // Legacy catalog IDs and Bologna IDs must never share persisted profiles.
  const profileKey = packageData ? "ytu-scheduler.bologna-programs.v1" : PROFILE_KEY;
  const [programs, setPrograms] = useState<Program[]>([]);
  const [profile, setProfile] = useState<number[] | null>(null);
  const [primary, setPrimary] = useState("");
  const [secondary, setSecondary] = useState("");
  const [doubleMajor, setDoubleMajor] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (packageData && !loading && !profile) heading.current?.focus();
  }, [packageData, loading, profile]);

  useEffect(() => {
    const controller = new AbortController();
    (packageData ? Promise.resolve(packageData.programs) : fetch(`${API_URL}/catalog/programs`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Bölümler yüklenemedi.");
        return response.json() as Promise<Program[]>;
      }))
      .then((available) => {
        if (controller.signal.aborted) return;
        setPrograms(available);
        try {
          const saved: unknown = JSON.parse(localStorage.getItem(profileKey) ?? "null");
          if (Array.isArray(saved) && saved.length >= 1 && saved.length <= 2 &&
              new Set(saved).size === saved.length &&
              saved.every((id) => Number.isInteger(id) && available.some((p) => p.id === id && p.curriculum_available))) {
            setProfile(saved);
            setPrimary(String(saved[0]));
            setSecondary(saved[1] ? String(saved[1]) : "");
            setDoubleMajor(saved.length === 2);
          }
        } catch { /* Browsers may disable local storage; selection still works. */ }
        setLoading(false);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setError("Bölümler yüklenemedi. Bağlantınızı kontrol edip yeniden deneyin.");
        setLoading(false);
      });
    return () => controller.abort();
  }, [attempt, packageData, profileKey]);

  const valid = programs.some((p) => p.id === Number(primary) && p.curriculum_available) &&
    (!doubleMajor || (secondary !== primary && programs.some((p) => p.id === Number(secondary) && p.curriculum_available)));

  function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid) return;
    const ids = doubleMajor ? [Number(primary), Number(secondary)] : [Number(primary)];
    try { localStorage.setItem(profileKey, JSON.stringify(ids)); } catch { /* Optional persistence. */ }
    setProfile(ids);
  }

  if (profile && packageData) return <Dashboard key={profile.join(",")} packageData={packageData} programIds={profile} onChangePrograms={() => setProfile(null)} />;

  if (profile) return <Dashboard programIds={profile} onChangePrograms={() => setProfile(null)} />;

  return (
    <main className="onboarding-shell">
      <form className="onboarding-card" onSubmit={confirm}>
        <span className="planner-logo">Y</span>
        <p className="onboarding-eyebrow">YTÜ Ders Planlayıcı</p>
        <h1 ref={heading} tabIndex={-1}>Bölümünüzü seçin</h1>
        <p>Planınızı oluşturmadan önce lütfen Lisans Bölümünüzü seçiniz.</p>
        {loading ? <p role="status">Bölümler yükleniyor…</p> : error ? (
          <div role="alert"><p>{error}</p><button type="button" className="gold-button"
            onClick={() => { setLoading(true); setError(null); setAttempt((value) => value + 1); }}>
            Yeniden dene
          </button></div>
        ) : !programs.length ? <p role="status">Bölüm verileri henüz yayınlanmadı.</p> : <>
          <MajorField label="Bölümünüz" programs={programs} value={primary}
            onChange={(value) => { setPrimary(value); if (value === secondary) setSecondary(""); }} />
          <label className="double-major-check">
            <input type="checkbox" checked={doubleMajor}
              onChange={(event) => setDoubleMajor(event.target.checked)} />
            ÇAP öğrencisiyim.
          </label>
          {doubleMajor && <MajorField label="İkinci bölümünüz"
            programs={programs.filter((program) => String(program.id) !== primary)}
            value={secondary} onChange={setSecondary} />}
          <button className="gold-button onboarding-confirm" type="submit" disabled={!valid}>Onayla</button>
        </>}
        <small>Sadece güncel bologna desteklenmektedir, ön koşullar ve ders kayıt şartlarının kontrolünden öğrenci sorumludur.</small>
      </form>
    </main>
  );
}
