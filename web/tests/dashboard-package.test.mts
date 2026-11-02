import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseStudentPackage } from '../src/lib/student-package.ts';
import { dashboardOfferings, dashboardResults } from '../src/lib/dashboard-package.ts';
import { solveSchedules } from '../src/lib/browser-solver.ts';
import { calendarExport } from '../src/lib/calendar-export.ts';
const data = () => parseStudentPackage(JSON.parse(readFileSync(new URL('./fixtures/student-package.json', import.meta.url), 'utf8')), { development: true, allowTest: true });

test('dashboard respects package eligibility and does not invent filter metadata', () => {
  const p = data();
  assert.equal(dashboardOfferings(p, [9]).length, 0);
  const offered = dashboardOfferings(p, [25, 9]);
  assert.equal(offered.length, 1);
  assert.deepEqual(offered[0].categories, []);
  assert.deepEqual(offered[0].compulsory_placements, []);
  assert.deepEqual(offered[0].options.map(o => o.id), p.groups.map(g => g.id));
});

test('dashboard solver selections preserve group IDs, every meeting, and ICS eligibility', async () => {
  const p = data();
  const offered = dashboardOfferings(p, [25]);
  const result = await solveSchedules(p, {programIds:[25],courseIds:offered.map(c=>c.course_id),locks:{},blocked:[],preference:'compact'});
  const view = dashboardResults(p, result);
  assert.equal(view.schedules.length, 1);
  const selections = view.schedules[0].selections;
  const groups = selections.map(s => p.groups.find(g=>g.id===s.option_id)!);
  assert.deepEqual(selections.flatMap(s=>s.meetings), result.alternatives[0].groups.flatMap(g=>g.meetings));
  assert.ok(calendarExport(p, groups).content.includes('BEGIN:VEVENT'));
});

test('limited and infeasible searches stay distinct and never invent calendar entries', () => {
  const p=data();
  const limited=dashboardResults(p,{status:'limited',alternatives:[],found:0,visited:5,reasons:[]});
  assert.equal(limited.truncated,true);
  assert.match(limited.message!,/çözüm olmadığı anlamına gelmez/);
  assert.equal(limited.schedules.length,0);
  const infeasible=dashboardResults(p,{status:'infeasible',alternatives:[],found:0,visited:0,reasons:['FIZ1111: grup yok']});
  assert.equal(infeasible.truncated,false);
  assert.match(infeasible.message!,/FIZ1111/);
});

test('filters match one major-specific role and unknown classifications remain unfiltered', async () => {
  const { matchesCurriculumRole } = await import('../src/lib/dashboard-package.ts');
  const roles = [
    {program:'Physics',category:'compulsory' as const,year:1,season:'fall' as const},
    {program:'Statistics',category:'professional_elective' as const,year:3,season:'spring' as const},
  ];
  assert.equal(matchesCurriculumRole(roles,{major:'all',category:'compulsory',year:'3',season:'all'}),false);
  assert.equal(matchesCurriculumRole(roles,{major:'Physics',category:'professional_elective',year:'all',season:'all'}),false);
  assert.equal(matchesCurriculumRole(roles,{major:'Statistics',category:'professional_elective',year:'3',season:'spring'}),true);
  assert.equal(matchesCurriculumRole([],{major:'all',category:'all',year:'all',season:'all'}),true);
  assert.equal(matchesCurriculumRole([],{major:'all',category:'all',year:'1',season:'all'}),false);
});

test('parser and dashboard preserve exported classifications without granting eligibility', () => {
  const raw=JSON.parse(readFileSync(new URL('./fixtures/student-package.json',import.meta.url),'utf8'));
  const p=raw.programs.find((p:{bologna_program_id:number})=>p.bologna_program_id===25);
  p.course_permissions[0].classifications=[{category:'compulsory',year:2,season:'spring'}];
  const parsed=parseStudentPackage(raw,{development:true,allowTest:true});
  assert.equal(dashboardOfferings(parsed,[25])[0].compulsory_placements[0].year,2);
  assert.equal(dashboardOfferings(parsed,[9]).length,0);
  p.course_permissions[0].classifications[0].year='2';
  assert.throws(()=>parseStudentPackage(raw,{development:true,allowTest:true}));
});
