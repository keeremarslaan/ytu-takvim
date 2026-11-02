import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseStudentPackage} from '../src/lib/student-package.ts';
import {solveSchedules,meetingsConflict} from '../src/lib/browser-solver.ts';
const p=parseStudentPackage(JSON.parse(readFileSync(new URL('./fixtures/dashboard-qa.json',import.meta.url),'utf8')),{development:true,allowTest:true});
const request=(ids:number[])=>({programIds:[25],courseIds:ids,locks:{},blocked:[],preference:'compact' as const});
test('rich dashboard scenario produces eight complete nonconflicting alternatives',async()=>{
 const r=await solveSchedules(p,request([1,2,3,4,5,6,8]));
 assert.equal(r.status,'complete');assert.equal(r.found,8);
 for(const a of r.alternatives){assert.equal(a.groups.length,7);for(let i=0;i<a.groups.length;i++)for(let j=i+1;j<a.groups.length;j++)assert.ok(!a.groups[i].meetings.some(x=>a.groups[j].meetings.some(y=>meetingsConflict(x,y))));}
 const tba=await solveSchedules(p,request([7]));assert.equal(tba.status,'infeasible');
});
