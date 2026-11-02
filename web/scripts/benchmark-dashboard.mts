import {readFileSync} from 'node:fs';
import {performance} from 'node:perf_hooks';
import {parseStudentPackage} from '../src/lib/student-package.ts';
import {solveSchedules} from '../src/lib/browser-solver.ts';
import {dashboardOfferings} from '../src/lib/dashboard-package.ts';
const p=parseStudentPackage(JSON.parse(readFileSync(new URL('../tests/fixtures/dashboard-qa.json',import.meta.url),'utf8')),{development:true,allowTest:true});
p.courses=[];p.groups=[];
for(let i=1;i<=300;i++){
 p.courses.push({catalog_course_id:i,code:`LOAD${i}`,name:`Synthetic ${i}`,prerequisite_text:null});
 const hour=8+Math.floor((i-1)/7)*2%14;
 for(let j=0;j<4;j++)p.groups.push({id:`load-${i}-${j}`,catalog_course_id:i,label:`G${j}`,instructor:null,language:null,time_status:'scheduled',eligible_bologna_program_ids:[25],meetings:[{weekday:(i-1)%7+1,starts_at:`${String(hour).padStart(2,'0')}:00:00`,ends_at:`${String(hour+1).padStart(2,'0')}:00:00`,room:null,delivery_mode:'in_person',effective_starts_on:p.term.starts_on,effective_ends_on:p.term.ends_on}]});
}
let start=performance.now();const rows=dashboardOfferings(p,[25]);const adapterMs=performance.now()-start;
let ticks=0;const timer=setInterval(()=>ticks++,1);start=performance.now();
const request={programIds:[25],courseIds:Array.from({length:12},(_,i)=>i+1),locks:{},blocked:[],preference:'compact' as const};
const result=await solveSchedules(p,request);clearInterval(timer);const elapsed=performance.now()-start;
const aborter=new AbortController();const cancel=setTimeout(()=>aborter.abort(),10);const cancellation=await solveSchedules(p,request,{signal:aborter.signal});clearTimeout(cancel);
console.log(JSON.stringify({courses:rows.length,groups:p.groups.length,adapterMs,solverMs:elapsed,status:result.status,visited:result.visited,alternatives:result.alternatives.length,eventLoopTicks:ticks,cancellation:cancellation.status},null,2));
if(result.status!=='limited'||result.alternatives.length===0||ticks===0||cancellation.status!=='cancelled')throw Error('Benchmark control regression');
