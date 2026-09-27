const {test}=require('node:test');
const assert=require('node:assert/strict');
const Touch=require('../hassan_ai/static/desktop-touch.js');
function setup() {
  const events=[];let time=0, serial=0;const timers=new Map();
  const touch=new Touch(e=>events.push(e),{
    later:fn=>{timers.set(++serial,fn);return serial;},cancel:id=>timers.delete(id),now:()=>time
  });
  return {touch,events,hold(){time+=450;for(const [id,fn] of timers){timers.delete(id);fn();}},tick(ms=40){time+=ms;}};
}
const p=(x=100,y=100)=>({clientX:x,clientY:y,position:{x:x/400,y:y/800}});
const buttons=events=>events.filter(e=>e.action==='button');
const scrolls=events=>events.filter(e=>e.action==='scroll');
test('tap sends no mouse-down before finger lift, then exactly one click',()=>{
  const {touch:t,events:e}=setup();t.down(1,p());assert.deepEqual(e,[]);
  t.up(1,p());assert.deepEqual(e,[{action:'move',...p().position},{action:'button',button:'left',down:true},{action:'button',button:'left',down:false}]);
});
test('small finger jitter remains a single tap',()=>{
  const {touch:t,events:e}=setup();t.down(1,p());t.move(1,p(103,103));t.up(1,p(104,104));assert.equal(buttons(e).length,2);assert.equal(scrolls(e).length,0);
});
test('one-finger swipe scrolls both directions without clicks or selection',()=>{
  const {touch:t,events:e,hold}=setup();t.down(1,p());t.move(1,p(100,148));hold();t.move(1,p(100,76));t.up(1,p(100,76));
  assert.deepEqual(scrolls(e),[{action:'scroll',dy:2},{action:'scroll',dy:-3}]);assert.deepEqual(buttons(e),[]);
  assert.equal(e.filter(x=>x.action==='move').length,1); // only anchor the hovered window, no cursor chasing
});
test('long press and drag selects, throttles moves, and releases at final position',()=>{
  const {touch:t,events:e,hold,tick}=setup();t.down(1,p());hold();assert.equal(t.mode,'selecting');
  for(let x=101;x<120;x++)t.move(1,p(x,100));assert.equal(e.filter(x=>x.action==='move').length,1);
  tick();t.move(1,p(160,100));t.up(1,p(200,100));
  assert.deepEqual(buttons(e),[{action:'button',button:'left',down:true},{action:'button',button:'left',down:false}]);
  assert.deepEqual(e.at(-2),{action:'move',...p(200,100).position});assert.equal(t.mode,'idle');
});
test('two fingers scroll with no click when either finger lifts',()=>{
  const {touch:t,events:e,hold}=setup();t.down(1,p());t.down(2,p(140,100));hold();
  t.move(1,p(100,148));t.move(2,p(140,148));t.up(2,p(140,148));t.move(1,p(100,220));t.up(1,p(100,220));
  assert.deepEqual(buttons(e),[]);assert.equal(scrolls(e).reduce((n,x)=>n+x.dy,0),2);
});
test('adding a second finger during selection releases the held button',()=>{
  const {touch:t,events:e,hold}=setup();t.down(1,p());hold();t.down(2,p(140,100));assert.deepEqual(buttons(e).map(x=>x.down),[true,false]);
  t.up(1,p());t.up(2,p(140,100));assert.equal(buttons(e).length,2);
});
test('cancel/blur/disconnect clears a pending hold and releases active selection once',()=>{
  const {touch:t,events:e,hold}=setup();t.down(1,p());t.reset();hold();assert.deepEqual(e,[]);
  t.down(1,p());hold();t.reset();t.reset();t.up(1,p());assert.deepEqual(buttons(e).map(x=>x.down),[true,false]);
});
test('three-finger gesture is ignored until all fingers lift',()=>{
  const {touch:t,events:e,hold}=setup();t.down(1,p());t.down(2,p(140,100));t.down(3,p(180,100));hold();t.move(1,p(100,200));
  t.up(3,p(180,100));t.up(2,p(140,100));t.up(1,p(100,200));assert.deepEqual(buttons(e),[]);assert.deepEqual(scrolls(e),[]);
});

test('large swipe steps stay within the server scroll limit',()=>{
  const {touch:t,events:e}=setup();t.down(1,p(100,300));t.move(1,p(100,700));t.move(1,p(100,0));t.up(1,p(100,0));
  assert.deepEqual(scrolls(e),[{action:'scroll',dy:5},{action:'scroll',dy:-5}]);assert.deepEqual(buttons(e),[]);
});
