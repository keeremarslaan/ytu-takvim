import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseStudentPackage } from '../src/lib/student-package.ts';
import { calendarExport } from '../src/lib/calendar-export.ts';
const fixture = () => parseStudentPackage(JSON.parse(readFileSync(new URL('./fixtures/student-package.json', import.meta.url), 'utf8')), { development: true, allowTest: true });
const unfold = (s: string) => s.replace(/\r\n /g, '');
const now = new Date('2026-09-19T12:00:00Z');
function sample() {
 const p = fixture(); p.groups = [p.groups[0]];
 p.groups[0].meetings = [{weekday: 1, starts_at: '09:00:00', ends_at: '10:00:00', effective_starts_on:'2026-09-22', effective_ends_on:'2026-10-12', room:'Davutpaşa', delivery_mode:'in_person'}];
 return p;
}
test('exact weekly dates, inclusive last day, Istanbul conversion, stable identity', () => {
 const p = sample(); const a = calendarExport(p,p.groups,now), b = calendarExport(p,p.groups,new Date());
 const s = unfold(a.content);
 assert.match(s,/DTSTART:20260928T060000Z/); assert.match(s,/DTEND:20260928T070000Z/);
 assert.match(s,/RRULE:FREQ=WEEKLY;COUNT=3/);
 assert.ok(!s.includes("RDATE:"));
 assert.equal(s.match(/UID:.*/)?.[0],unfold(b.content).match(/UID:.*/)?.[0]);
 assert.match(s,/DTSTAMP:20260919T120000Z/); assert.match(a.filename,/-TEST.ics$/);
 assert.ok(!a.content.replace(/\r\n/g,'').includes('\n'));
});
test('every meeting including weekends, online and a single occurrence', () => {
 const p = sample(); p.groups[0].meetings.push({...p.groups[0].meetings[0],weekday:7,effective_starts_on:'2026-10-04',effective_ends_on:'2026-10-04',delivery_mode:'online'});
 const s = unfold(calendarExport(p,p.groups,now).content);
 assert.equal(s.match(/BEGIN:VEVENT/g)?.length,2); assert.equal(s.match(/RRULE:/g)?.length,1);
 assert.match(s,/DTSTART:20261004T060000Z/); assert.match(s,/Çevrim içi/);
});
test('UTF-8 byte folding and injection-safe text round trip', () => {
 const p = sample(); const value = 'Şğİ😀'.repeat(40)+';x,y\\z\nEND:VEVENT'; p.courses[0].name=value;
 p.groups[0].catalog_course_id=p.courses[0].catalog_course_id;
 const {content} = calendarExport(p,p.groups,now);
 for(const line of content.split('\r\n')) assert.ok(Buffer.byteLength(line)<=75);
 const s=unfold(content); assert.ok(s.includes('Şğİ😀'.repeat(40)));
 assert.ok(s.includes('\\;x\\,y\\\\z\\nEND:VEVENT'));
 assert.equal(s.split('\r\n').filter(l=>l==='END:VEVENT').length,1);
});
test('empty, stale and TBA selections fail without a partial download', () => {
 const p=sample(); assert.throws(()=>calendarExport(p,[]));
 assert.throws(()=>calendarExport(p,[{...p.groups[0],id:'stale'}]));
 p.groups[0].time_status='tba'; assert.throws(()=>calendarExport(p,p.groups));
});
test('rejects unsupported fractional seconds and dates outside the verified term', () => {
 const p=sample(); p.groups[0].meetings[0].starts_at='09:00:00.1'; assert.throws(()=>calendarExport(p,p.groups));
 p.groups[0].meetings[0].starts_at='09:00:00.000'; assert.doesNotThrow(()=>calendarExport(p,p.groups));
 p.groups[0].meetings[0].effective_ends_on='2030-01-01'; assert.throws(()=>calendarExport(p,p.groups));
});
test('midnight Istanbul maps to previous UTC date, preserves seconds and ignores machine timezone', () => {
 const p=sample(); p.groups[0].meetings[0].starts_at='00:00:01'; p.groups[0].meetings[0].ends_at='00:30:02';
 const s=unfold(calendarExport(p,p.groups,now).content);
 assert.match(s,/DTSTART:20260927T210001Z/); assert.match(s,/DTEND:20260927T213002Z/);
});

test('historical Istanbul offset changes split weekly rules without shifting wall time', () => {
 const p=sample(); p.term.starts_on='2015-03-01'; p.term.ends_on='2015-04-30';
 const m=p.groups[0].meetings[0]; m.effective_starts_on='2015-03-16'; m.effective_ends_on='2015-04-06';
 const s=unfold(calendarExport(p,p.groups,now).content);
 assert.equal(s.match(/BEGIN:VEVENT/g)?.length,2);
 assert.match(s,/DTSTART:20150316T070000Z/); assert.match(s,/DTSTART:20150330T060000Z/);
 assert.equal(s.match(/RRULE:FREQ=WEEKLY;COUNT=2/g)?.length,2);
 assert.equal(new Set(s.match(/UID:.*/g)).size,2);
});
