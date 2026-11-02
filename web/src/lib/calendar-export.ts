import type { PackageGroup, StudentPackage } from "./student-package.ts";

const DAY = 86400000;
const encoder = new TextEncoder();
const wallClock = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Istanbul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
function text(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/\r\n|\r|\n/g, "\\n").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
}
function fold(line: string): string {
  let out = "", width = 0;
  for (const char of line) {
    const size = encoder.encode(char).length;
    if (width + size > 75) { out += "\r\n "; width = 1; }
    out += char; width += size;
  }
  return out;
}
function stamp(date: Date): string { return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z"); }
function localMillis(value: number): number {
  const p = Object.fromEntries(wallClock.formatToParts(value).map(p => [p.type, p.value]));
  return Date.parse(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}Z`);
}
function instant(date: string, time: string): number {
  if (!/^\d{2}:\d{2}:\d{2}(?:\.0+)?$/.test(time)) throw new Error("Takvim aktarımı tam saniye hassasiyetinde ders saatleri gerektirir.");
  const wall = Date.parse(`${date}T${time.split(".")[0]}Z`);
  const utc = wall - (localMillis(wall) - wall);
  if (!Number.isFinite(utc) || localMillis(utc) !== wall) throw new Error("Ders saati İstanbul saat dilimine dönüştürülemedi.");
  return utc;
}

/** Input is the validated package and a complete solver alternative from that package. */
export function calendarExport(data: StudentPackage, selected: PackageGroup[], now = new Date()): { content: string; filename: string } {
  if (data.purpose === "weekly_preview" || !data.term.starts_on || !data.term.ends_on) throw new Error("Takvim indirmek için dönem tarihleri belirlenmelidir.");
  if (!selected.length || selected.length > 40 || new Set(selected.map(g => g.catalog_course_id)).size !== selected.length) throw new Error("Takvime aktarılacak geçerli bir program seçin.");
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//YTU Scheduler//Student Calendar//TR", "CALSCALE:GREGORIAN", `X-WR-CALNAME:${text(`YTÜ ${data.term.code}${data.purpose === "test" ? " — TEST" : ""}`)}`, "X-WR-TIMEZONE:Europe/Istanbul"];
  for (const chosen of [...selected].sort((a, b) => a.id.localeCompare(b.id))) {
    const group = data.groups.find(g => g.id === chosen.id);
    const course = data.courses.find(c => c.catalog_course_id === group?.catalog_course_id);
    if (!group || !course || group.time_status !== "scheduled" || !group.meetings.length || group.meetings.length > 50) throw new Error("Program verisi güncel değil. Programı yeniden oluşturun.");
    for (const [index, meeting] of group.meetings.entries()) {
      if (!meeting.effective_starts_on || !meeting.effective_ends_on) throw new Error("Takvim indirmek için ders tarihleri belirlenmelidir.");
      const from = Date.parse(meeting.effective_starts_on), to = Date.parse(meeting.effective_ends_on);
      if (!Number.isFinite(from) || !Number.isFinite(to) || from > to || to - from > 370 * DAY || meeting.effective_starts_on < data.term.starts_on || meeting.effective_ends_on > data.term.ends_on) throw new Error("Ders tarihleri takvim aktarımı için geçersiz.");
      const first = from + ((meeting.weekday - (new Date(from).getUTCDay() || 7) + 7) % 7) * DAY;
      const starts: number[] = [], ends: number[] = [];
      for (let day = first; day <= to; day += 7 * DAY) {
        const date = new Date(day).toISOString().slice(0, 10);
        starts.push(instant(date, meeting.starts_at)); ends.push(instant(date, meeting.ends_at));
      }
      if (!starts.length || ends.some((end, i) => end <= starts[i] || end - starts[i] !== ends[0] - starts[0])) throw new Error("Ders süresi takvim aktarımı için geçersiz.");
      const description = [data.purpose === "test" ? "TEST VERİSİ — gerçek ders programı değildir." : "", `Grup: ${group.label}`, group.instructor && `Öğretim elemanı: ${group.instructor}`, group.language && `Dil: ${group.language}`, `Ders biçimi: ${{ in_person: "Yüz yüze", online: "Çevrim içi", hybrid: "Hibrit", unknown: "Belirtilmemiş" }[meeting.delivery_mode] ?? meeting.delivery_mode}`, "Saat dilimi: Europe/Istanbul", `Dönem: ${data.term.code}`].filter(Boolean).join("\n");
      // Apple Calendar drops RDATE-only repeats. Use weekly rules, split at any
      // timezone offset change so each segment still preserves Istanbul wall time.
      const segments: number[][] = [];
      starts.forEach((start, i) => {
        if (i === 0 || start - starts[i - 1] !== 7 * DAY) segments.push([]);
        segments[segments.length - 1].push(i);
      });
      for (const segment of segments) {
        const firstIndex = segment[0];
        const suffix = segments.length > 1 ? `-${stamp(new Date(starts[firstIndex]))}` : "";
        lines.push("BEGIN:VEVENT", `UID:${data.export_id}-${group.id}-${index}${suffix}@ytu-scheduler.invalid`, `DTSTAMP:${stamp(now)}`, `DTSTART:${stamp(new Date(starts[firstIndex]))}`, `DTEND:${stamp(new Date(ends[firstIndex]))}`);
        if (segment.length > 1) lines.push(`RRULE:FREQ=WEEKLY;COUNT=${segment.length}`);
        lines.push(`SUMMARY:${text(`${data.purpose === "test" ? "[TEST] " : ""}${course.code} — ${course.name} (${group.label})`)}`, `DESCRIPTION:${text(description)}`);
        if (meeting.room) lines.push(`LOCATION:${text(meeting.room)}`);
        lines.push("END:VEVENT");
      }
    }
  }
  lines.push("END:VCALENDAR");
  return { content: lines.map(fold).join("\r\n") + "\r\n", filename: `ytu-${data.term.code.replace(/[^a-zA-Z0-9_-]/g, "_")}${data.purpose === "test" ? "-TEST" : ""}.ics` };
}
