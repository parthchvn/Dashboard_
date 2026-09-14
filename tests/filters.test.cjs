'use strict';
const assert=require('node:assert/strict');
const {dateWindow,displayDates,parseWallets}=require('../xvi/static/filters.js');
const day=86400;
const one=dateWindow('2024-03-10','2024-03-10');
assert.equal(one.end-one.start,day);
assert.equal(new Date(one.start*1000).toISOString(),'2024-03-10T00:00:00.000Z');
assert.equal(new Date(one.end*1000).toISOString(),'2024-03-11T00:00:00.000Z');
assert.equal(dateWindow('2024-11-03','2024-11-03').end-dateWindow('2024-11-03','2024-11-03').start,day);
assert.equal(dateWindow('2024-02-28','2024-03-01').end-dateWindow('2024-02-28','2024-03-01').start,3*day);
assert.deepEqual(displayDates(one.start,one.end),{initial:'2024-03-10',final:'2024-03-10'});
assert.deepEqual(displayDates(one.start+61,one.start+90),{initial:'2024-03-10',final:'2024-03-10'});
assert.throws(()=>dateWindow('2024-02-30','2024-03-01'),/valid/);
assert.throws(()=>dateWindow('2023-02-29','2024-03-01'),/valid/);
assert.throws(()=>dateWindow('2024-03-11','2024-03-10'),/on or after/);
assert.throws(()=>dateWindow('','2024-03-01'),/both/);
assert.throws(()=>dateWindow('2100-01-01','2100-01-01'),/valid/);
assert.equal(dateWindow('1970-01-01','1970-01-01').start,0);
const a='0x'+'a'.repeat(40),b='0x'+'b'.repeat(40);
assert.deepEqual(parseWallets(`${b}; ${a.toUpperCase()}\n${a}`),[a,b]);
assert.deepEqual(parseWallets('  '),[]);
assert.throws(()=>parseWallets('0x123'),/full wallet/);
assert.throws(()=>parseWallets(`${a},invalid`),/full wallet/);
assert.throws(()=>parseWallets(' '.repeat(8193)),/long/);
assert.throws(()=>parseWallets(Array.from({length:51},(_,i)=>'0x'+i.toString(16).padStart(40,'0')).join(',')),/50/);
console.log('Filter semantics: 19 assertions passed (UTC inclusive dates, DST, invalid input, exact wallet OR).');
const {normalizeLevel,granularity,granularityControls}=require('../xvi/static/filters.js');
for(const level of ['auto','raw','30s','1m','2m','5m','10m','15m','30m','1h','4h','1d','7m']) {
  assert.equal(normalizeLevel(level),level);
  const controls=granularityControls(level);
  assert.equal(granularity(controls.choice,controls.minutes),level);
}
for(const [value,expected] of [['1','1m'],['2','2m'],['3','3m'],['7','7m'],['60','1h'],['240','4h'],['1440','1d']]) {
  assert.equal(granularity('custom',value),expected);
}
for(const invalid of ['', '0','-1','1.5','1441','1e2','01','NaN','2m']) assert.throws(()=>granularity('custom',invalid),/whole number/);
for(const invalid of ['__proto__','constructor','../../raw','2m;DROP','1441m','auto()']) assert.throws(()=>normalizeLevel(invalid),/granularity/);
assert.deepEqual(granularityControls('7m'),{choice:'custom',minutes:'7'});
console.log('Granularity semantics passed: presets, custom integer minutes, canonical aliases, restored controls, invalid values.');
