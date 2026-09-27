const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../hassan_ai/static/desktop-control.js'),'utf8');
const start=source.indexOf('  function point(event, clamp=false) {');
const end=source.indexOf('  function touchData(event)',start);
function mapping(capture,rect={left:0,top:0,width:400,height:300},natural={width:1600,height:900}) {
  const view={dataset:{capture:JSON.stringify(capture)},naturalWidth:natural.width,naturalHeight:natural.height,getBoundingClientRect:()=>rect};
  return vm.runInNewContext(`(${source.slice(start,end).trim()})`,{view,JSON,Math,Number});
}
const desktop={left:-1600,top:-120,width:3520,height:1200};
const left={left:-1600,top:-120,width:1600,height:900};
test('selected monitor pixels map onto the full virtual desktop including negative origins',()=>{
  const point=mapping({monitor:left,virtual:desktop});
  const first=point({clientX:0,clientY:37.5});
  const last=point({clientX:400,clientY:262.5});
  assert.equal(first.x,0); assert.equal(first.y,0);
  assert.equal(last.x,1599/3519); assert.equal(last.y,899/1199);
});
test('primary monitor mapping does not accidentally click a secondary monitor',()=>{
  const primary={left:0,top:0,width:1920,height:1080};
  const point=mapping({monitor:primary,virtual:desktop});
  const value=point({clientX:0,clientY:37.5});
  assert.equal(value.x,1600/3519);assert.equal(value.y,120/1199);
});
test('letterboxes remain rejected, dragging may clamp only to the chosen monitor edge',()=>{
  const point=mapping({monitor:left,virtual:desktop});
  assert.equal(point({clientX:200,clientY:10}),null);
  const clamped=point({clientX:600,clientY:400},true);
  assert.equal(clamped.x,1599/3519); assert.equal(clamped.y,899/1199);
});
test('whole-desktop views preserve normalized input without capture metadata',()=>{
  const point=mapping({monitor:null,virtual:null});
  const value=point({clientX:200,clientY:150});
  assert.equal(value.x,0.5);assert.equal(value.y,0.5);
});
