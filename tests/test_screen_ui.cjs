const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../hassan_ai/static/index.html'),'utf8');
const source=html.slice(html.indexOf('let scrOn = false, scrTask = null'),html.indexOf('async function scrSend()'));
const settle=async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
function setup({response,reject,connection}={}) {
  const calls=[],events=[],elements={};
  for(const name of ['screenQuality','screenMonitor','screenPreferenceNote','screenOptionsRefresh','scrView','scrBox','scrFull','scrBtn','scrErr'])elements[name]={value:name==='screenQuality'?'auto':name==='screenMonitor'?'desktop':'',style:{},dataset:{},options:[],replaceChildren(){this.options=[];},add(option){this.options.push(option);}};
  const context=vm.createContext({$:id=>elements[id],navigator:{connection},document:{dispatchEvent:event=>events.push(event.type)},Event:class{constructor(type){this.type=type;}},Option:class{constructor(text,value){this.text=text;this.value=value;}},URL,location:{href:'https://pc.test/'},JSON,Promise,
    api:async(path,options={})=>{const call={path,body:options.body?JSON.parse(options.body):undefined};calls.push(call);
      if(path==='/api/screen/options')return{monitors:[{id:'desktop',label:'All'},{id:'monitor-2',label:'Second'}]};
      if(path==='/api/live/screen'){if(reject)throw Error('Another viewer owns the stream');return response?await response:{url:'/api/live/screen?token=owned-token',stream_id:'owned-token',capture:{left:0,top:0,width:1920,height:1080},virtual:{left:0,top:0,width:1920,height:1080}};}
      return{};
    }});
  vm.runInContext(source,context);return{calls,events,elements,context};
}
test('busy screen rejection never sends an unowned stop request',async()=>{
  const app=setup({reject:true});await settle();await app.elements.scrBtn.onclick();
  assert.equal(app.calls.filter(c=>c.path==='/api/live/screen/stop').length,0);
  assert.equal(app.elements.scrView.dataset.streaming,'false');
});
test('automatic selection is bounded and only chosen at stream start',async()=>{
  const app=setup({connection:{effectiveType:'3g'}});await settle();await app.elements.scrBtn.onclick();
  assert.deepEqual(app.calls.find(c=>c.path==='/api/live/screen').body,{profile:'economy',monitor:'desktop'});
  app.elements.screenQuality.value='sharp';app.elements.screenQuality.onchange();
  assert.equal(app.calls.filter(c=>c.path==='/api/live/screen').length,1);
  assert.match(app.elements.screenPreferenceNote.textContent,/للعرض القادم/);
});
test('stopping a view presents only its own stream capability',async()=>{
  const app=setup();await settle();await app.elements.scrBtn.onclick();await app.elements.scrBtn.onclick();
  assert.deepEqual(app.calls.find(c=>c.path==='/api/live/screen/stop').body,{token:'owned-token'});
  assert.equal(app.elements.scrView.dataset.capture,undefined);
});
test('cancelled pending opens clean up only the newly acquired stream',async()=>{
  let resolve;const response=new Promise(r=>{resolve=r;});
  const app=setup({response});await settle();const opening=app.elements.scrBtn.onclick();await settle();
  await vm.runInContext('scrStop()',app.context);
  assert.equal(app.calls.filter(c=>c.path==='/api/live/screen/stop').length,0);
  resolve({url:'/api/live/screen?token=late-token',stream_id:'late-token'});await opening;
  assert.deepEqual(app.calls.find(c=>c.path==='/api/live/screen/stop').body,{token:'late-token'});
  assert.equal(app.elements.scrView.dataset.streaming,'false');
});
test('selected monitor metadata remains tied to the current frame after a preference change',async()=>{
  const app=setup();await settle();app.elements.screenMonitor.value='monitor-2';await app.elements.scrBtn.onclick();
  const before=app.elements.scrView.dataset.capture;app.elements.screenMonitor.value='desktop';app.elements.screenMonitor.onchange();
  assert.equal(app.elements.scrView.dataset.capture,before);
  assert.equal(app.calls.find(c=>c.path==='/api/live/screen').body.monitor,'monitor-2');
});
