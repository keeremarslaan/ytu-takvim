import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseStudentPackage, type PackageGroup, type PackageMeeting, type StudentPackage } from "../src/lib/student-package.ts";
import { solveSchedules, meetingsConflict, type SolverRequest } from "../src/lib/browser-solver.ts";
const fixture = () => parseStudentPackage(JSON.parse(readFileSync(new URL("./fixtures/student-package.json", import.meta.url), "utf8")), { development: true, allowTest: true });
const meeting = (weekday = 1, start = "09:00:00", end = "10:00:00", from = "2026-09-21", to = "2026-10-04"): PackageMeeting => ({ weekday, starts_at: start, ends_at: end, effective_starts_on: from, effective_ends_on: to, room: null, delivery_mode: "in_person" });
const group = (cid: number, name: string, meetings = [meeting()]): PackageGroup => ({ id: name, catalog_course_id: cid, label: name, instructor: null, language: null, time_status: meetings.length ? "scheduled" : "tba", meetings, eligible_bologna_program_ids: [25] });
function data(groups: PackageGroup[]): StudentPackage {
  const p = fixture(); p.groups = groups; p.courses = [...new Set(groups.map(g => g.catalog_course_id))].map(id => ({ catalog_course_id: id, code: `C${id}`, name: `Course ${id}`, prerequisite_text: null })); return p;
}
const request = (ids: number[], overrides: Partial<SolverRequest> = {}): SolverRequest => ({ courseIds: ids, programIds: [25], locks: {}, blocked: [], preference: "compact", ...overrides });
const fast = { maxMs: 5000, yieldTask: async () => {} };

test("exporter package produces a schedule without API", async () => {
 const p = fixture(), r = await solveSchedules(p, request([p.courses[0].catalog_course_id]), fast);
 assert.equal(r.status, "complete"); assert.equal(r.alternatives[0].groups[0].id, p.groups[0].id);
});
test("enumerates compatible combinations, selects exactly one group per course", async () => {
 const r = await solveSchedules(data([group(1,"a"), group(1,"b",[meeting(2)]), group(2,"c"), group(2,"d",[meeting(3)])]), request([1,2]), fast);
 assert.equal(r.status,"complete"); assert.equal(r.found,3);
 for(const a of r.alternatives) { assert.equal(new Set(a.groups.map(g=>g.catalog_course_id)).size,2); assert.ok(!meetingsConflict(a.groups[0].meetings[0], a.groups[1].meetings[0])); }
});
test("boundary adjacency allowed, seconds and all seven days honored", () => {
 assert.equal(meetingsConflict(meeting(7),meeting(7,"10:00:00","11:00:00")),false);
 assert.equal(meetingsConflict(meeting(7),meeting(7,"09:59:59.9","11:00:00")),true);
 assert.equal(meetingsConflict(meeting(6),meeting(7)),false);
});
test("date overlap must contain an actual shared weekday", () => {
 assert.equal(meetingsConflict(meeting(1,"09:00:00","10:00:00","2026-09-21","2026-09-27"), meeting(1,"09:00:00","10:00:00","2026-09-22","2026-09-28")),false);
 assert.equal(meetingsConflict(meeting(),meeting(1,"09:00:00","10:00:00","2026-09-28","2026-10-04")),true);
});
test("checks every meeting and online conflicts", async () => {
 const online = {...meeting(7),delivery_mode:"online"};
 const r=await solveSchedules(data([group(1,"a",[meeting(2),online]),group(2,"b",[meeting(7)])]),request([1,2]),fast);
 assert.equal(r.status,"infeasible");
});
test("locks cannot be relaxed, stale and ineligible locks reject", async () => {
 const p=data([group(1,"a"),group(1,"b",[meeting(2)]),group(2,"c")]);
 assert.equal((await solveSchedules(p,request([1,2],{locks:{1:"a"}}),fast)).status,"infeasible");
 assert.equal((await solveSchedules(p,request([1,2],{locks:{1:"b"}}),fast)).found,1);
 await assert.rejects(solveSchedules(p,request([1],{locks:{1:"missing"}}),fast));
 p.groups[1].eligible_bologna_program_ids=[9];
 await assert.rejects(solveSchedules(p,request([1],{locks:{1:"b"}}),fast));
});
test("weekly blocked intervals apply to weekends and every meeting", async () => {
 const r=await solveSchedules(data([group(1,"a",[meeting(7)]),group(1,"b",[meeting(6)])]),request([1],{blocked:[{weekday:7,starts_at:"09:30",ends_at:"11:00"}]}),fast);
 assert.equal(r.found,1); assert.equal(r.alternatives[0].groups[0].id,"b");
});
test("TBA excluded even if locked; no empty fabricated schedule", async () => {
 const p=data([group(1,"tba",[]),group(1,"known")]);
 assert.equal((await solveSchedules(p,request([1]),fast)).found,1);
 const r=await solveSchedules(p,request([1],{locks:{1:"tba"}}),fast); assert.equal(r.status,"infeasible"); assert.equal(r.alternatives.length,0);
});
test("ranks actual teaching dates and gaps, including online", async () => {
 const p=data([group(1,"a"),group(2,"compact",[meeting(1,"10:00:00","11:00:00")]),group(2,"gap",[meeting(1,"12:00:00","13:00:00")]),group(2,"other-day",[meeting(2)])]);
 const r=await solveSchedules(p,request([1,2],{preference:"few_days"}),fast);
 assert.equal(r.alternatives[0].groups[1].id,"compact"); assert.equal(r.alternatives[0].metrics.teachingDays,2); assert.equal(r.alternatives.find(a=>a.groups[1].id==="gap")?.metrics.gapMinutes,240);
});
test("early/late preferences order alternatives", async () => {
 const p=data([group(1,"early",[meeting(1,"08:00:00","09:00:00")]),group(1,"late",[meeting(1,"18:00:00","19:00:00")])]);
 assert.equal((await solveSchedules(p,request([1],{preference:"late_start"}),fast)).alternatives[0].groups[0].id,"late");
 assert.equal((await solveSchedules(p,request([1],{preference:"early_finish"}),fast)).alternatives[0].groups[0].id,"early");
});
test("node limit is not infeasibility and keeps only complete assignments", async () => {
 const p=data([group(1,"a"),group(1,"b",[meeting(2)]),group(2,"c",[meeting(3)])]);
 const r=await solveSchedules(p,request([1,2]),{...fast,maxNodes:1}); assert.equal(r.status,"limited"); assert.equal(r.alternatives.length,0);
 const partial=await solveSchedules(p,request([1,2]),{...fast,maxNodes:2}); assert.equal(partial.status,"limited"); assert.ok(partial.alternatives.every(a=>a.groups.length===2));
});
test("deadline and preparation ceilings return limited", async () => {
 let t=0; assert.equal((await solveSchedules(data([group(1,"a")]),request([1]),{...fast,now:()=>t+=10,maxMs:1})).status,"limited");
 const p=data([group(1,"a")]); p.term.ends_on="2030-01-01"; assert.equal((await solveSchedules(p,request([1]),fast)).status,"limited");
});
test("cooperative yielding observes cancellation", async () => {
 const controller=new AbortController();
 const r=await solveSchedules(data([group(1,"a")]),request([1]),{signal:controller.signal,yieldTask:async()=>{controller.abort();}});
 assert.equal(r.status,"cancelled");
});
test("invalid requests fail instead of relaxing constraints", async () => {
 const p=data([group(1,"a")]);
 for(const r of [request([]),request([1,1]),request([999]),request([1],{locks:{2:"a"}}),request([1],{blocked:[{weekday:1,starts_at:"11:00",ends_at:"10:00"}]})]) await assert.rejects(solveSchedules(p,r,fast));
});
test("returns deterministic best twelve after exhaustive enumeration", async () => {
 const p=data(Array.from({length:20},(_,i)=>group(1,`g${String(i).padStart(2,"0")}`)));
 const a=await solveSchedules(p,request([1]),fast); p.groups.reverse(); const b=await solveSchedules(p,request([1]),fast);
 assert.equal(a.found,20); assert.equal(a.alternatives.length,12); assert.deepEqual(a,b);
});

test("search agrees with independent exhaustive oracle across fifty small timetables", async () => {
  let seed = 721;
  function random(n: number) { seed = (seed * 16807) % 2147483647; return seed % n; }
  for (let sample = 0; sample < 50; sample++) {
    const groups = Array.from({length: 9}, (_, i) => group(Math.floor(i / 3) + 1, `g${i}`, [meeting(random(3) + 1, `${9 + random(2)}`.padStart(2, "0") + ":00:00", "11:00:00")]));
    let expected = 0;
    for (const a of groups.slice(0, 3)) for (const b of groups.slice(3, 6)) for (const c of groups.slice(6)) {
      const days = [a, b, c].map(g => g.meetings[0].weekday);
      if (new Set(days).size === 3) expected++;
    }
    const result = await solveSchedules(data(groups), request([1, 2, 3]), fast);
    assert.equal(result.found, expected);
    assert.equal(result.status, expected ? "complete" : "infeasible");
  }
});


test("conflict feedback names locked groups and actual meeting times", async () => {
 const r = await solveSchedules(data([group(1,"a"),group(1,"free",[meeting(2)]),group(2,"b",[meeting(1,"09:30:00","11:00:00")])]), request([1,2],{locks:{1:"a"}}),fast);
 assert.equal(r.status,"infeasible");
 for(const text of ["C1 (Course 1)","C2 (Course 2)","a [kilitli]","Pazartesi 09:00–10:00","Pazartesi 09:30–11:00","tüm grup eşleşmeleri"]) assert.ok(r.reasons.join(" ").includes(text),text);
});
test("blocked feedback distinguishes locked, all-blocked and unknown times", async () => {
 const p=data([group(1,"a",[meeting(6)]),group(1,"b",[meeting(7)])]);
 const blocked=[{weekday:6,starts_at:"09:30",ends_at:"10:30"},{weekday:7,starts_at:"09:00",ends_at:"10:00"}];
 const r=await solveSchedules(p,request([1],{locks:{1:"a"},blocked}),fast);
 for(const text of ["a [kilitli]","ders Cumartesi 09:00–10:00","engellenen saat Cumartesi 09:30–10:30"]) assert.ok(r.reasons[0].includes(text),text);
 const all=await solveSchedules(p,request([1],{blocked}),fast);
 assert.match(all.reasons[0],/tüm uygun gruplar/); assert.match(all.reasons[0],/Pazar/);
 const tba=await solveSchedules(data([group(1,"unknown",[])]),request([1],{locks:{1:"unknown"}}),fast);
 assert.match(tba.reasons[0],/saatleri henüz belli değil/); assert.doesNotMatch(tba.reasons[0],/çakış/);
});
test("higher-order infeasibility does not blame a compatible course pair", async () => {
 const p=data([1,2,3].flatMap(id=>[group(id,`${id}a`),group(id,`${id}b`,[meeting(2)])]));
 const r=await solveSchedules(p,request([1,2,3]),fast);
 assert.equal(r.status,"infeasible"); assert.match(r.reasons[0],/Tek bir ders çifti/); assert.doesNotMatch(r.reasons[0],/tüm grup eşleşmeleri/);
});
test("limited and cancelled searches do not report proven conflicts", async () => {
 const p=data([group(1,"a"),group(2,"b")]);
 const limited=await solveSchedules(p,request([1,2]),{...fast,maxNodes:1});
 assert.equal(limited.status,"limited"); assert.deepEqual(limited.reasons,[]);
 const controller=new AbortController(); controller.abort();
 const cancelled=await solveSchedules(p,request([1,2]),{...fast,signal:controller.signal});
 assert.equal(cancelled.status,"cancelled"); assert.deepEqual(cancelled.reasons,[]);
});
