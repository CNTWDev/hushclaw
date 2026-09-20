import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../hushclaw/web/modules/calendar_math.js',import.meta.url),'utf8');
const {dayKey,shiftDay,wallToISO,wallInput,segments,layoutIntervals,gaps,conflictCount}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const event=(a,b,all_day=false)=>({event_id:a,start_time:a,end_time:b,all_day});

test('wall-time conversion respects configured zone, not browser zone',()=>{
  assert.equal(wallToISO('2026-09-20T09:00','Asia/Shanghai'),'2026-09-20T01:00:00.000Z');
  assert.equal(dayKey('2026-09-20T01:00:00Z','America/Los_Angeles'),'2026-09-19');
  assert.equal(wallInput('2026-09-20T01:00:00Z','Asia/Shanghai'),'2026-09-20T09:00');
  assert.equal(shiftDay('2026-12-31',1),'2027-01-01');
  assert.throws(()=>wallToISO('2026-03-08T02:30','America/Los_Angeles'));
});
test('all-day end is exclusive and date-only events do not shift zones',()=>{
  const events=[event('2026-09-20','2026-09-22',true)];
  assert.equal(segments(events,'2026-09-20','America/Los_Angeles').length,1);
  assert.equal(segments(events,'2026-09-21','Asia/Shanghai').length,1);
  assert.equal(segments(events,'2026-09-22','Asia/Shanghai').length,0);
});
test('cross-midnight events clip on each day and exclude exact end boundary',()=>{
  const events=[event('2026-09-20T15:00:00Z','2026-09-21T01:00:00Z')];
  const a=segments(events,'2026-09-20','Asia/Shanghai')[0],b=segments(events,'2026-09-21','Asia/Shanghai')[0];
  assert.equal(a.start,1380);assert.equal(a.end,1440);assert.equal(a.continues,true);
  assert.equal(b.start,0);assert.equal(b.end,540);assert.equal(b.continued,true);
  assert.equal(segments([event('2026-09-19T15:00:00Z','2026-09-19T16:00:00Z')],'2026-09-20','Asia/Shanghai').length,0);
});
test('overlap layout assigns independent columns and resets after a group',()=>{
  const rows=[{start:540,end:660},{start:570,end:600},{start:600,end:690},{start:690,end:720}];
  assert.deepEqual(layoutIntervals(rows).map(r=>[r.column,r.columns]),[[0,2],[1,2],[1,2],[0,1]]);
  assert.equal(conflictCount(rows),2);
  assert.deepEqual(gaps(rows),[[720,1080]]);
  assert.deepEqual(gaps([{allDay:true}]),[]);
});
test('DST transition days preserve next-midnight clipping',()=>{
  const row=segments([event('2026-03-08T08:00:00Z','2026-03-09T07:00:00Z')],'2026-03-08','America/Los_Angeles')[0];
  assert.equal(row.start,0);assert.equal(row.end,1440);
});
