const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../hassan_ai/static/transfer-panel.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../hassan_ai/static/transfer-panel.html'),'utf8');
const settle=async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
function setup({ready=true,failClipboard=false}={}) {
  class Element {
    constructor(){this.value='';this.dataset={};this.children=[];this.handlers={};this.files=[];this.open=false;}
    replaceChildren(){this.children=[];} append(...children){this.children.push(...children);}
    addEventListener(type,fn){this.handlers[type]=fn;}
    focus(){this.focused=true;} select(){this.selected=true;}
  }
  const elements=Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const calls=[];
  const fetch=async(url,options)=>{
    calls.push({url,options});
    if(url==='/api/transfers')return{ok:true,json:async()=>({files:[]})};
    if(url==='/api/desktop')return{ok:true,json:async()=>({enabled:ready,connected:ready})};
    if(url.startsWith('/api/transfers?'))return{ok:true,json:async()=>({name:'chosen.txt'})};
    if(url.startsWith('/api/clipboard/'))return{ok:!failClipboard,json:async()=>failClipboard?{detail:'الجهاز الآخر يتحكم الآن'}:{text:'نص من الكمبيوتر'}};
    throw Error('Unexpected network request');
  };
  vm.runInNewContext(source,{document:{hidden:false,getElementById:id=>elements[id],createElement:()=>new Element(),addEventListener(){}},fetch,encodeURIComponent,JSON,setInterval(){},Error});
  return{elements,calls};
}
test('page load reads file/status metadata but never clipboard or browser clipboard permissions',async()=>{
  const app=setup();await settle();
  assert.deepEqual(app.calls.map(c=>c.url),['/api/transfers','/api/desktop']);
  assert.equal(app.elements.transferText.value,'');
});
test('choosing a file alone does not upload and an explicit oversized upload is refused locally',async()=>{
  const app=setup();await settle();
  app.elements.transferFile.files=[{name:'large.bin',size:25*1024*1024+1}];
  assert.equal(app.calls.length,2);
  await app.elements.transferUpload.onclick();
  assert.equal(app.calls.filter(c=>c.url.startsWith('/api/transfers?')).length,0);
});
test('explicit upload sends the original file bytes separately from encoded filename',async()=>{
  const app=setup();await settle();const file={name:'اختبار & file.txt',size:50};
  app.elements.transferFile.files=[file];await app.elements.transferUpload.onclick();
  const request=app.calls.find(c=>c.url.startsWith('/api/transfers?'));
  assert.equal(request.url,'/api/transfers?name='+encodeURIComponent(file.name));
  assert.equal(request.options.body,file);
  assert.equal(request.options.headers['Content-Type'],'application/octet-stream');
});
test('explicit clipboard read populates only the visible text field',async()=>{
  const app=setup();await settle();await app.elements.transferClipboardRead.onclick();
  assert.equal(app.elements.transferText.value,'نص من الكمبيوتر');
  assert.equal(app.calls.filter(c=>c.url==='/api/clipboard/read').length,1);
  app.elements.transferSelectText.onclick();
  assert.equal(app.elements.transferText.selected,true);
  assert.equal(app.calls.length,3);
});
test('clipboard write is explicit and never dispatches paste input',async()=>{
  const app=setup();await settle();app.elements.transferText.value='مرحبا';
  await app.elements.transferClipboardWrite.onclick();
  const request=app.calls.find(c=>c.url==='/api/clipboard/write');
  assert.deepEqual(JSON.parse(request.options.body),{text:'مرحبا'});
  assert.equal(app.calls.length,3);
});
test('permission failures preserve user text and disabled control sends no clipboard requests',async()=>{
  const app=setup({failClipboard:true});await settle();app.elements.transferText.value='احتفظ بهذا';
  await app.elements.transferClipboardRead.onclick();
  assert.equal(app.elements.transferText.value,'احتفظ بهذا');
  assert.equal(app.elements.transferClipboardStatus.dataset.error,'true');
  const unavailable=setup({ready:false});await settle();await unavailable.elements.transferClipboardRead.onclick();
  assert.equal(unavailable.calls.length,2);
});
