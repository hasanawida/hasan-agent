/* Manual input on the existing screen stream. No credentials in URLs/storage. */
(() => {
  'use strict';
  const el = id => document.getElementById(id), view = el('scrView');
  let socket = null, ready = false, state = null, pollBusy = false, beat = null, movingAt = 0, opening = false;
  let immersive=false, more=false, zoomed=false, panning=false;
  const surface=el('desktopSurface'), viewport=el('desktopViewport');
  const held = new Map();
  const pressedPointers = new Set();
  async function request(path, method='GET', body) {
    const options={method,credentials:'same-origin'};
    if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
    const response = await fetch(path, options);
    const data = await response.json().catch(()=>({}));
    if (!response.ok) throw Error(data.detail || 'تعذّر الاتصال');
    return data;
  }
  function message(text) { el('desktopState').textContent=text; }
  function send(data) {
    if (!ready || socket?.readyState!==WebSocket.OPEN) return false;
    if (socket.bufferedAmount>65536) { stop('الاتصال بطيء؛ توقف التحكّم.',false); return false; }
    socket.send(JSON.stringify(data));return true;
  }
  const touch=new DesktopTouch(send,{changed:mode=>{view.dataset.gesture=mode;}});
  function release() { touch.reset();send({action:'release'}); held.clear(); pressedPointers.clear(); }
  function render() {
    el('desktopTools').hidden=!ready || (immersive && !more);
    el('desktopZoomTools').hidden=!immersive || !more;
    el('desktopFullConnect').textContent=ready?'⏹ تحكّم':'تحكّم';
    el('desktopFullConnect').disabled=opening || (socket ? !ready : !state?.enabled);
    el('desktopKeyboard').disabled=!ready;
    el('desktopFullStatus').textContent=ready?'متصل':socket?'جارٍ الاتصال…':state?.enabled?'عرض فقط':'السماح مطفأ';
    if(!ready)hideKeyboard();
    view.dataset.control=String(ready);
    el('desktopConnect').textContent=socket?'وقف التحكّم':opening?'جارٍ فتح الشاشة…':'افتح الشاشة وتحكّم';
    el('desktopConnect').disabled=opening || (!socket && !state?.enabled);
    if (!state) return;
    el('desktopGrant').hidden=!state.local || !state.supported;
    el('desktopEnable').textContent=state.enabled?'إلغاء السماح بالتحكّم':'السماح بالتحكّم لمدة 30 دقيقة';
    el('desktopAlways').textContent=state.persistent?'السماح الدائم مفعّل':'السماح الدائم لأجهزتي';
    el('desktopAlways').disabled=state.persistent;
    el('desktopBanner').hidden=!state.enabled;
    document.body.classList.toggle('desktopGranted',state.enabled);
    el('desktopBannerText').textContent=`التحكّم اليدوي ${state.connected?'متصل':'مسموح لأجهزتك'} · ${state.persistent?'سماح دائم':`متبقي ${Math.ceil(state.expires_in/60)} دقيقة`}`;
  }
  async function refresh() {
    if(pollBusy)return;pollBusy=true;
    try {
      state=await request('/api/desktop');
      if (!state.enabled && socket) stop('انتهت صلاحية التحكّم أو تم إيقافه من الكمبيوتر.');
      if(!socket){
        if(!state.supported)message('التحكّم اليدوي يحتاج Windows وتحديث مكتبات Hassan. عرض الشاشة يبقى متاحًا.');
        else if(!state.enabled)message(state.local?'فعّل السماح، ثم اعرض الشاشة وابدأ التحكّم.':'فعّل السماح بالتحكّم من صفحة Hassan على الكمبيوتر أولًا.');
        else if(state.connected)message('جهاز آخر يتحكّم الآن. أنهِ جلسته قبل بدء جلسة جديدة.');
        else message(state.persistent?'جاهز دائمًا؛ اضغط افتح الشاشة وتحكّم من هاتفك.':'اضغط افتح الشاشة وتحكّم.');
      }
      render();
    } catch(error) { if(!socket)message(error.message); }
    finally {pollBusy=false;}
  }
  function stop(text='توقّف التحكّم. عرض الشاشة مستمر.',flush=true) {
    // Release before removing the socket; the server also releases on close.
    if(flush)release(); const old=socket;socket=null;ready=false;touch.reset();held.clear();pressedPointers.clear();clearInterval(beat);old?.close();
    message(text);render();
  }
  async function disable() {
    stop('جارٍ إيقاف التحكّم…');
    try {await request('/api/desktop/disable','POST');await refresh();}
    catch(error){message(error.message);}
  }
  el('desktopEnable').onclick=async()=>{
    if(state?.enabled)return disable();
    el('desktopEnable').disabled=true;
    try {state=await request('/api/desktop/enable','POST');await refresh();}
    catch(error){message(error.message);}
    finally{el('desktopEnable').disabled=false;}
  };
  el('desktopAlways').onclick=async()=>{
    el('desktopAlways').disabled=true;
    try {state=await request('/api/desktop/enable','POST',{persistent:true});await refresh();}
    catch(error){message(error.message);}
    finally{render();}
  };
  el('desktopEmergency').onclick=disable;
  function sizeSurface() {
    const vv=window.visualViewport;
    surface.style.setProperty('--screen-height',`${vv?.height || window.innerHeight}px`);
    surface.style.setProperty('--screen-top',`${vv?.offsetTop || 0}px`);
    if(immersive && view.naturalWidth && zoomed) {
      const ratio=view.naturalWidth/view.naturalHeight;
      const base=Math.min(viewport.clientWidth,viewport.clientHeight*ratio);
      view.style.width=`${base*2}px`;view.style.height=`${base*2/ratio}px`;
    } else {view.style.width='';view.style.height='';}
  }
  function enterFull() {
    immersive=true;surface.classList.add('immersive');document.body.classList.add('desktopImmersive');document.documentElement.classList.add('desktopImmersive');
    more=false;render();sizeSurface();
    // Keep a CSS fullscreen fallback for browsers without the Fullscreen API.
    const enter=surface.requestFullscreen || surface.webkitRequestFullscreen;
    if(enter && !matchMedia('(max-width:900px)').matches && !document.fullscreenElement) {
      try {Promise.resolve(enter.call(surface)).catch(()=>{});}catch{}
    }
  }
  function leaveFull() {
    hideKeyboard();release();immersive=false;zoomed=false;panning=false;more=false;
    surface.classList.remove('immersive');document.body.classList.remove('desktopImmersive');document.documentElement.classList.remove('desktopImmersive');
    view.dataset.pan='false';viewport.classList.remove('zoomed');
    el('desktopZoom').textContent='تكبير ×2';el('desktopPan').disabled=true;
    if(document.fullscreenElement===surface)document.exitFullscreen?.().catch(()=>{});
    render();sizeSurface();
  }
  function hideKeyboard() {
    el('desktopKeyboardPanel').hidden=true;el('desktopKeyboard').setAttribute('aria-expanded','false');
    if(document.activeElement===el('desktopText'))el('desktopText').blur();
  }
  function showKeyboard() {
    if(!ready)return;
    release();el('desktopKeyboardPanel').hidden=false;more=false;el('desktopTools').hidden=immersive;
    el('desktopKeyboard').setAttribute('aria-expanded','true');
    // Synchronous focus inside this tap is required to open a phone's keyboard.
    el('desktopText').focus({preventScroll:true});render();sizeSurface();
  }
  el('desktopLeaveFull').onclick=leaveFull;
  el('desktopKeyboard').onclick=()=>el('desktopKeyboardPanel').hidden?showKeyboard():hideKeyboard();
  el('desktopInlineKeyboard').onclick=showKeyboard;
  el('desktopHideKeyboard').onclick=hideKeyboard;
  el('desktopFullConnect').onclick=()=>el('desktopConnect').click();
  el('desktopMore').onclick=()=>{more=!more;hideKeyboard();el('desktopMore').setAttribute('aria-expanded',String(more));render();};
  el('desktopZoom').onclick=()=>{zoomed=!zoomed;panning=zoomed;release();viewport.classList.toggle('zoomed',zoomed);
    view.dataset.pan=String(panning);el('desktopZoom').textContent=zoomed?'ملاءمة الشاشة':'تكبير ×2';
    el('desktopPan').disabled=!zoomed;el('desktopPan').textContent=panning?'العودة للنقر':'تحريك العرض';sizeSurface();};
  el('desktopPan').onclick=()=>{panning=!panning;release();view.dataset.pan=String(panning);
    el('desktopPan').textContent=panning?'العودة للنقر':'تحريك العرض';};
  document.addEventListener('hassan:screen-fullscreen',enterFull);
  document.addEventListener('hassan:screen-opening',()=>{if(matchMedia('(max-width:900px)').matches)enterFull();});
  document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement && immersive)leaveFull();});
  window.addEventListener('resize',sizeSurface);window.visualViewport?.addEventListener('resize',sizeSurface);
  window.visualViewport?.addEventListener('scroll',sizeSurface);view.addEventListener('load',()=>{sizeSurface();render();});
  new ResizeObserver(sizeSurface).observe(viewport);
  el('desktopDisconnect').onclick=()=>stop();
  el('desktopConnect').onclick=async()=>{
    if(socket)return stop();
    if(opening || !state?.enabled)return;
    opening=true;render();
    try {
      if(view.dataset.streaming!=='true')await el('scrBtn').onclick();
      if(view.dataset.streaming!=='true')return;
      if(!view.naturalWidth)await new Promise((resolve,reject)=>{
        const cleanup=()=>{clearTimeout(timeout);view.removeEventListener('load',loaded);view.removeEventListener('error',failed);document.removeEventListener('hassan:screen-stopped',failed);};
        const loaded=()=>{cleanup();resolve();};
        const failed=()=>{cleanup();reject(Error('تعذّر فتح الشاشة. جرّب مرة ثانية.'));};
        const timeout=setTimeout(failed,15000);
        view.addEventListener('load',loaded,{once:true});view.addEventListener('error',failed,{once:true});
        document.addEventListener('hassan:screen-stopped',failed,{once:true});
      });
    } catch(error){message(error.message);return;}
    finally{opening=false;render();}
    if(!state?.enabled || view.dataset.streaming!=='true')return;
    if(location.protocol!=='https:'&&!['127.0.0.1','localhost','[::1]'].includes(location.hostname)){
      message('للتحكّم عن بُعد، افتح رابط HTTPS الخاص بـ Tailscale.');return;
    }
    const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/desktop/control`);
    socket=ws;render();message('جارٍ توصيل الماوس والكيبورد…');let failure='';
    const timeout=setTimeout(()=>{if(socket===ws&&!ready)stop('تعذّر بدء التحكّم. جرّب مرة أخرى.');},10000);
    ws.onmessage=event=>{
      if(socket!==ws)return;
      let data;try{data=JSON.parse(event.data);}catch{stop('رد غير صالح من الخادم.');return;}
      if(data.type==='ready'){
        ready=true;clearTimeout(timeout);message('التحكّم مفعّل. انقر الشاشة أو اكتب من لوحة الهاتف.');render();
        beat=setInterval(()=>send({action:'heartbeat'}),2000);
      } else if(data.type==='error'){failure=data.message;}
    };
    ws.onclose=()=>{clearTimeout(timeout);if(socket===ws)stop(failure||'توقّف التحكّم أو انقطع الاتصال.');refresh();};
    ws.onerror=()=>{failure='تعذّر الاتصال. تحقق من صلاحية الدخول وتفعيل التحكّم ورابط HTTPS.';};
  };
  function point(event, clamp=false) {
    const rect=view.getBoundingClientRect(),scale=Math.min(rect.width/view.naturalWidth,rect.height/view.naturalHeight);
    const width=view.naturalWidth*scale,height=view.naturalHeight*scale;
    const x=(event.clientX-rect.left-(rect.width-width)/2)/width,y=(event.clientY-rect.top-(rect.height-height)/2)/height;
    if(!Number.isFinite(x)||!Number.isFinite(y)||(!clamp&&(x<0||x>1||y<0||y>1)))return null;
    return {x:Math.max(0,Math.min(1,x)),y:Math.max(0,Math.min(1,y))};
  }
  function touchData(event) {return {clientX:event.clientX,clientY:event.clientY,position:point(event,true)};}
  view.addEventListener('pointerdown',event=>{
    if(!ready||panning||event.button>2)return;
    const position=point(event);if(!position)return;
    event.preventDefault();view.focus({preventScroll:true});pressedPointers.add(event.pointerId);view.setPointerCapture(event.pointerId);
    if(event.pointerType==='touch'){touch.down(event.pointerId,touchData(event));return;}
    send({action:'move',...position});send({action:'button',button:['left','middle','right'][event.button],down:true});
  });
  view.addEventListener('pointermove',event=>{
    if(!ready||panning||!pressedPointers.has(event.pointerId))return;
    if(event.pointerType==='touch') {
      event.preventDefault();touch.move(event.pointerId,touchData(event));return;
    }
    if(performance.now()-movingAt<40)return;
    const position=point(event,true);if(!position)return;
    movingAt=performance.now();send({action:'move',...position});
  });
  view.addEventListener('pointerup',event=>{
    if(!ready||panning||!pressedPointers.has(event.pointerId))return;
    event.preventDefault();
    if(event.pointerType==='touch')touch.up(event.pointerId,touchData(event));
    else {
      const position=point(event,true);if(position)send({action:'move',...position});
      send({action:'button',button:['left','middle','right'][event.button],down:false});
    }
    pressedPointers.delete(event.pointerId);
    if(view.hasPointerCapture(event.pointerId))view.releasePointerCapture(event.pointerId);
  });
  view.addEventListener('pointercancel',release);
  view.addEventListener('lostpointercapture',event=>{if(pressedPointers.has(event.pointerId))release();});
  view.addEventListener('contextmenu',event=>{if(ready)event.preventDefault();});
  view.addEventListener('dragstart',event=>event.preventDefault());
  view.addEventListener('wheel',event=>{if(ready){event.preventDefault();send({action:'scroll',dy:-Math.sign(event.deltaY)});}},{passive:false});
  view.addEventListener('keydown',event=>{
    if(!ready||event.isComposing)return;
    event.preventDefault();
    if(event.key==='Escape'){release();view.blur();message('تحرّرت لوحة المفاتيح. انقر الشاشة لاستئناف الكتابة.');return;}
    const key=held.get(event.code)||event.key;
    if(key.length!==1&&!['Enter','Backspace','Tab','Delete','Insert','Home','End','PageUp','PageDown','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Shift','Control','Alt','Meta','CapsLock'].includes(key)&&!/^F([1-9]|1[0-2])$/.test(key))return;
    held.set(event.code,key);send({action:'key',key,down:true});
  });
  view.addEventListener('keyup',event=>{if(!ready)return;event.preventDefault();const key=held.get(event.code);if(key)send({action:'key',key,down:false});held.delete(event.code);});
  view.addEventListener('blur',release);window.addEventListener('blur',release);
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&socket)stop('توقّف التحكّم لأن الصفحة لم تعد ظاهرة.');});
  window.addEventListener('pagehide',()=>stop());
  document.addEventListener('hassan:screen-stopped',()=>{leaveFull();stop('توقّف عرض الشاشة والتحكّم.');});
  function tap(key){send({action:'key',key,down:true});send({action:'key',key,down:false});}
  document.querySelectorAll('[data-desktop-key]').forEach(button=>button.onclick=()=>tap(button.dataset.desktopKey));
  document.querySelectorAll('[data-desktop-shortcut]').forEach(button=>button.onclick=()=>{
    release();send({action:'shortcut',name:button.dataset.desktopShortcut});
  });
  el('desktopRunShortcut').onclick=()=>{release();send({action:'shortcut',name:el('desktopShortcut').value});};
  el('desktopRight').onclick=()=>{send({action:'button',button:'right',down:true});send({action:'button',button:'right',down:false});};
  el('desktopUp').onclick=()=>send({action:'scroll',dy:3});el('desktopDown').onclick=()=>send({action:'scroll',dy:-3});
  function sendText(){const text=el('desktopText').value;if(text&&ready&&send({action:'text',text})){el('desktopText').value='';el('desktopText').focus({preventScroll:true});}}
  el('desktopSendText').onclick=sendText;
  el('desktopText').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();sendText();}});
  refresh();setInterval(refresh,2000);
})();
