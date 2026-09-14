const assert=require('node:assert/strict');
const {carryPieces,toPoints,clamp}=require('../xvi/static/chart.js');
assert.deepEqual(carryPieces({time:100,observedAt:100},1000,30),[
  {start:100,end:130,stale:false},{start:130,end:220,stale:true}
]);
assert.deepEqual(carryPieces({time:500,observedAt:100},1000,30),[]);
assert.deepEqual(carryPieces({time:100,observedAt:100},100,30),[]);
const points=toPoints({level:'30s',end:125,rows:[{bin_end:150,last_trade_ts:121,close:.6,low:.4,high:.7,notional:10}]});
assert.equal(points[0].time,125);
assert.equal(points[0].observedAt,121);
assert.equal(points[0].value,.6);
assert.equal(clamp(200,0,100),100);
console.log('Chart semantics: 7 assertions passed.');
