import { existsSync } from "node:fs";
import type { NextConfig } from "next";
import { PHASE_PRODUCTION_BUILD } from "next/constants";

export default function nextConfig(phase: string): NextConfig {
  // `next typegen` loads the production config but does not create a release artifact.
  const buildingRelease = phase === PHASE_PRODUCTION_BUILD && !process.argv.includes("typegen");
  const previews = ["pilot-weekly-preview.json", "qa-student-preview.json", "demo-student-package.json"];
  if (buildingRelease && previews.some(file => existsSync(`public/${file}`))) {
    throw new Error("Remove generated local preview files from public/ before building a release.");
  }
  if (buildingRelease && !process.env.NEXT_PUBLIC_STUDENT_PACKAGE_URL?.trim()) {
    throw new Error("Set NEXT_PUBLIC_STUDENT_PACKAGE_URL to the reviewed student-data URL before building the static student site.");
  }

  return { output: "export" };
}
