"use client";

import { useEffect, useState } from "react";
import { ProgramOnboarding } from "./program-onboarding";
import { loadStudentPackage, type StudentPackage } from "../lib/student-package";
const PACKAGE_URL = process.env.NEXT_PUBLIC_STUDENT_PACKAGE_URL;

export function StudentApp() {
  if (!PACKAGE_URL?.trim()) {
    if (process.env.NODE_ENV === "development") return <ProgramOnboarding />;
    return <p role="alert">Ders verileri henüz yayımlanmadı. Lütfen daha sonra tekrar deneyin.</p>;
  }
  return <PackageLoader url={PACKAGE_URL} />;
}

function PackageLoader({ url }: { url: string }) {
  const [data, setData] = useState<StudentPackage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    loadStudentPackage(url, controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Ders verileri yüklenemedi.");
    });
    return () => controller.abort();
  }, [url, attempt]);
  function refresh() { setData(null); setError(null); setAttempt(a => a + 1); }
  return <div className="student-package-app">
    <div className="package-banner" role="status">
      {data ? `${data.term.academic_year} · ${data.term.season === "fall" ? "Güz" : data.term.season === "spring" ? "Bahar" : "Yaz"}` : error ?? "Ders verileri yükleniyor…"}
      {data?.purpose === "weekly_preview" && <strong>Yerel deneme · 4 bölümün gerçek dersleri · Haftalık program</strong>}
      {data?.purpose === "test" && <strong>Örnek veriler — gerçek ders kaydı için kullanmayın.</strong>}
      {(data || error) && <button type="button" className="gold-button" onClick={refresh}>Verileri yenile</button>}
      {data && <small>Yenileme, seçili dersleri temizler.</small>}
    </div>
    {data && <ProgramOnboarding key={data.export_id} packageData={data} />}
  </div>;
}
