/* Explicit, authenticated phone sessions. Screen and input never start on page load. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('androidCard')) return;
  const image = $('phoneFrame'), screen = $('phoneScreen');
  let devices = [], local = false, listBusy = false, switching = false, taskBusy = false;
  let selected = '', owner = null, ownerDevice = '', sessionEpoch = 0, sessionBusy = false;
  let viewing = false, frameEpoch = 0, frameTimer = null, frameAbort = null, blobUrl = null, frameAt = 0;
  let leaseTimer = null, leaseBusy = false, commandBusy = false, pointer = null, pairTimer = null;

  async function request(path, method = 'GET', body, extra = {}) {
    const options = {method, credentials: 'same-origin', cache: 'no-store', ...extra};
    if (body !== undefined) {
      options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(body);
    }
    const abort = new AbortController();
    options.signal = abort.signal;
    const timeout = setTimeout(() => abort.abort(), 10000);
    let response, data;
    try {
      response = await fetch(path, options);
      data = await response.json().catch(() => ({}));
    } catch (error) {
      if (error.name === 'AbortError') throw Error('الاتصال بطيء؛ تعذّر تأكيد الطلب.');
      throw error;
    } finally { clearTimeout(timeout); }
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : '';
      throw Error(detail || (response.status === 401 ? 'انتهت جلسة الدخول؛ حدّث الصفحة وسجّل الدخول.' : 'تعذّر تنفيذ الطلب على التلفون.'));
    }
    return data;
  }
  const endpoint = (id, suffix) => `/api/phones/${encodeURIComponent(id)}/${suffix}`;
  const device = () => devices.find(item => item.device_id === selected);
  const allowed = d => Boolean(d?.online && d.control_enabled);
  const message = (text, error = false) => {
    $('phoneMessage').textContent = text;
    $('phoneMessage').dataset.error = String(error);
  };
  function render() {
    const d = device(), manual = Boolean(owner), occupied = Boolean(d?.busy && !manual);
    $('phonePair').disabled = !local;
    $('phoneDevice').disabled = !devices.length || switching || sessionBusy || taskBusy;
    $('phoneView').disabled = switching || taskBusy || (!viewing && (!d?.online || !d.screen_enabled));
    $('phoneView').textContent = viewing ? 'إخفاء العرض هنا' : 'اعرض شاشة التلفون';
    $('phoneControl').disabled = switching || taskBusy || sessionBusy || (!manual && (!allowed(d) || occupied));
    $('phoneControl').textContent = sessionBusy ? 'جارٍ الاتصال…' : manual ? 'وقف تحكّمي' : 'ابدأ التحكّم';
    $('phoneStopScreen').disabled = sessionBusy || switching || taskBusy || !d?.online || !d.screen_enabled || occupied;
    $('phoneManual').hidden = !manual;
    const inputsReady = manual && viewing && Boolean(d?.screen_enabled) && Boolean(frameAt);
    document.querySelectorAll('[data-phone-key]').forEach(button => { button.disabled = !inputsReady; });
    $('phoneSendText').disabled = !inputsReady; $('phoneEnter').disabled = !inputsReady;
    $('phoneText').disabled = !inputsReady;
    screen.dataset.control = String(manual);
    $('phoneGestureHint').textContent = manual ? 'اضغط للنقر، واسحب بإصبعك أو بزر الماوس للتمرير على التلفون.' : 'العرض فقط. اضغط «ابدأ التحكّم» لتستخدم النقر والسحب.';
    $('phoneRunTask').disabled = taskBusy || switching || sessionBusy || !allowed(d) || occupied;
    $('phoneRunTask').textContent = taskBusy ? 'جارٍ تشغيل المهمة…' : 'شغّل المهمة على هذا التلفون';
    $('phoneDeviceStatus').dataset.online = String(Boolean(d?.online));
    if (!d) $('phoneDeviceStatus').textContent = devices.length ? 'اختار التلفون.' : 'ما في تلفون مربوط بعد. افتح «ربط تلفون جديد» لتبدأ.';
    else if (!d.online) $('phoneDeviceStatus').textContent = 'التلفون غير متصل. افتح تطبيق Hassan على التلفون وتأكد من اتصال الإنترنت وTailscale.';
    else if (!d.screen_enabled) $('phoneDeviceStatus').textContent = 'التلفون متصل؛ اضغط «ابدأ المشاركة» ووافق على مشاركة الشاشة من التلفون.';
    else if (!d.control_enabled) $('phoneDeviceStatus').textContent = 'عرض الشاشة متاح. فعّل خدمة Hassan ضمن «تسهيل الاستخدام» على التلفون للسماح بالتحكّم.';
    else if (manual) $('phoneDeviceStatus').textContent = 'أنت تتحكّم بالتلفون الآن.';
    else if (occupied) $('phoneDeviceStatus').textContent = d.owner_kind === 'agent' ? 'الإيجنت يستخدم التلفون الآن. تابع المهمة أو أوقفها قبل التحكّم اليدوي.' : 'جلسة أخرى تستخدم التلفون الآن. أنهِ الجلسة أولًا.';
    else $('phoneDeviceStatus').textContent = 'التلفون متصل وجاهز. العرض والتحكّم يبدأوا بكبسة منك.';
  }
  async function refresh() {
    if (listBusy || document.hidden) return;
    listBusy = true;
    try {
      const data = await request('/api/phones');
      devices = Array.isArray(data.devices) ? data.devices.map(d => ({...d, device_id: d.device_id || d.id})) : [];
      local = data.local === true;
      const old = selected;
      if (!devices.some(d => d.device_id === selected)) selected = devices[0]?.device_id || '';
      if (old && old !== selected) { stopViewing(); await stopManual(); }
      const select = $('phoneDevice');
      select.replaceChildren();
      if (!devices.length) select.add(new Option('ما في هواتف مربوطة', ''));
      for (const d of devices) select.add(new Option(`${d.label || 'تلفون Android'} · ${d.online ? 'متصل' : 'غير متصل'}`, d.device_id));
      select.value = selected;
      const d = device();
      if (viewing && (!d?.online || !d.screen_enabled)) stopViewing();
      if (owner && !allowed(d)) { await stopManual(); message('توقّف التحكّم لأن التلفون فصل أو ألغى السماح.', true); }
      $('phonePairHelp').textContent = local ? 'الرمز مؤقت ويُستخدم مرة واحدة. أدخله داخل تطبيق الهاتف.' : 'لإنشاء رمز ربط، افتح صفحة Hassan على الكمبيوتر نفسه.';
      render();
    } catch (error) { message(error.message, true); }
    finally { listBusy = false; }
  }
  function stopViewing() {
    viewing = false; frameEpoch++; frameAt = 0; pointer = null;
    clearTimeout(frameTimer); frameAbort?.abort(); frameAbort = null;
    image.removeAttribute('src'); image.hidden = true;
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    blobUrl = null;
    $('phoneScreenWrap').hidden = true;
    $('phoneFrameWait').hidden = false;
    render();
  }
  async function nextFrame(epoch, id) {
    if (!viewing || epoch !== frameEpoch || document.hidden || id !== selected) return;
    const abort = new AbortController(); frameAbort = abort;
    const timeout = setTimeout(() => abort.abort(), 8000);
    try {
      const response = await fetch(endpoint(id, 'frame'), {credentials: 'same-origin', cache: 'no-store', signal: abort.signal});
      if (!response.ok) throw Error(response.status === 404 ? 'بانتظار مشاركة الشاشة من التلفون…' : 'تعذّر تحديث صورة التلفون.');
      if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('image/jpeg')) throw Error('وصلت صورة غير صالحة من التلفون.');
      const blob = await response.blob();
      if (!viewing || epoch !== frameEpoch || document.hidden || id !== selected) return;
      if (blob.size > 8 * 1024 * 1024) throw Error('صورة التلفون كبيرة جدًا.');
      const previous = blobUrl;
      blobUrl = URL.createObjectURL(blob);
      image.src = blobUrl;
      image.hidden = false;
      if (previous) URL.revokeObjectURL(previous);
      // The load event marks this frame usable after the browser decodes it.
      frameAt = 0;
    } catch (error) {
      if (viewing && epoch === frameEpoch && !document.hidden) {
        frameAt = 0;
        $('phoneFrameWait').hidden = false;
        $('phoneFrameWait').textContent = error.name === 'AbortError' ? 'الاتصال بطيء؛ عم نحاول نحدّث الصورة…' : error.message;
      }
    } finally {
      clearTimeout(timeout);
      if (frameAbort === abort) frameAbort = null;
      if (viewing && epoch === frameEpoch && !document.hidden) frameTimer = setTimeout(() => nextFrame(epoch, id), 500);
    }
  }
  function startViewing() {
    if (viewing || !device()?.online || !device()?.screen_enabled || document.hidden) return;
    viewing = true;
    const epoch = ++frameEpoch;
    $('phoneScreenWrap').hidden = false;
    $('phoneFrameWait').hidden = false;
    $('phoneFrameWait').textContent = 'بانتظار صورة من التلفون…';
    render(); nextFrame(epoch, selected);
  }
  async function stopManual({keepalive = false, quiet = false} = {}) {
    sessionEpoch++; pointer = null;
    const previous = owner, id = ownerDevice;
    owner = null; ownerDevice = ''; clearInterval(leaseTimer); leaseTimer = null;
    render();
    if (!previous) return true;
    try {
      await request(endpoint(id, 'session/stop'), 'POST', {owner: previous}, {keepalive});
      return true;
    } catch (error) {
      if (!quiet) message(`توقّف إرسال الأوامر، لكن تعذّر تأكيد إنهاء الجلسة: ${error.message}`, true);
      return false;
    }
  }
  async function renewLease() {
    if (!owner || leaseBusy) return;
    const previous = owner, id = ownerDevice;
    leaseBusy = true;
    try {
      await request(endpoint(id, 'session'), 'POST', {owner: previous});
    } catch (error) {
      if (owner === previous) { await stopManual(); message(`توقّف التحكّم: ${error.message}`, true); }
    } finally { leaseBusy = false; }
  }
  async function startManual() {
    if (sessionBusy || owner || !allowed(device()) || document.hidden) return;
    sessionBusy = true; render();
    const id = selected, epoch = ++sessionEpoch;
    try {
      const data = await request(endpoint(id, 'session'), 'POST', {});
      if (!data.owner || typeof data.owner !== 'string') throw Error('تعذّر تأكيد جلسة التحكّم.');
      if (epoch !== sessionEpoch || document.hidden || selected !== id) {
        await request(endpoint(id, 'session/stop'), 'POST', {owner: data.owner}, {keepalive: true});
        return;
      }
      owner = data.owner; ownerDevice = id;
      leaseTimer = setInterval(renewLease, 15000);
      startViewing(); message(device()?.screen_enabled ? 'بدأ التحكّم. اضغط أو اسحب على شاشة التلفون.' : 'جلسة التحكّم جاهزة. ابدأ عرض الشاشة من تطبيق الهاتف لتستخدم النقر والسحب.');
    } catch (error) { message(error.message, true); }
    finally { sessionBusy = false; render(); }
  }
  async function command(action, args) {
    if (!owner || commandBusy || document.hidden || !allowed(device())) return false;
    if (!viewing || !frameAt || Date.now() - frameAt > 10000) { message('انتظر حتى تتحدّث صورة التلفون قبل إرسال أمر.', true); return false; }
    const previous = owner, id = ownerDevice;
    commandBusy = true;
    try {
      await request(endpoint(id, 'command'), 'POST', {owner: previous, action, args});
      return true;
    } catch (error) { message(error.message, true); return false; }
    finally { commandBusy = false; }
  }
  // object-fit:contain creates letterbox areas. They must never become phone taps.
  function point(event) {
    if (!image.naturalWidth || !image.naturalHeight || image.hidden) return null;
    const rect = image.getBoundingClientRect();
    const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
    const width = image.naturalWidth * scale, height = image.naturalHeight * scale;
    if (!width || !height) return null;
    const x = (event.clientX - rect.left - (rect.width - width) / 2) / width;
    const y = (event.clientY - rect.top - (rect.height - height) / 2) / height;
    return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? {x, y} : null;
  }
  screen.addEventListener('pointerdown', event => {
    if (!owner || commandBusy || !event.isPrimary || event.button !== 0) { pointer = null; return; }
    const p = point(event);
    if (!p) return;
    event.preventDefault();
    pointer = {id: event.pointerId, point: p, x: event.clientX, y: event.clientY, at: Date.now(), width: image.naturalWidth, height: image.naturalHeight};
    screen.setPointerCapture(event.pointerId);
  });
  screen.addEventListener('pointerup', event => {
    const start = pointer;
    if (!start || start.id !== event.pointerId) return;
    pointer = null; event.preventDefault();
    if (screen.hasPointerCapture(event.pointerId)) screen.releasePointerCapture(event.pointerId);
    const end = point(event);
    if (!end || start.width !== image.naturalWidth || start.height !== image.naturalHeight) return;
    const distance = Math.hypot(event.clientX - start.x, event.clientY - start.y);
    if (distance < 7) command('tap', start.point);
    else command('swipe', {x1: start.point.x, y1: start.point.y, x2: end.x, y2: end.y, duration_ms: Math.max(100, Math.min(1500, Date.now() - start.at))});
  });
  screen.addEventListener('pointercancel', () => { pointer = null; });
  screen.addEventListener('lostpointercapture', () => { pointer = null; });
  screen.addEventListener('contextmenu', event => { if (owner) event.preventDefault(); });
  image.addEventListener('load', () => {
    if (viewing && !image.hidden) { frameAt = Date.now(); $('phoneFrameWait').hidden = true; render(); }
  });
  image.addEventListener('error', () => {
    if (viewing) { frameAt = 0; $('phoneFrameWait').hidden = false; $('phoneFrameWait').textContent = 'تعذّر عرض صورة التلفون؛ عم نحاول من جديد…'; }
  });
  $('phoneView').onclick = async () => {
    if (viewing) { stopViewing(); message('تم إخفاء العرض هنا. لإيقاف مشاركة الشاشة نفسها استخدم زر الإيقاف في تطبيق الهاتف.'); }
    else startViewing();
  };
  $('phoneControl').onclick = async () => {
    if (owner) { if (await stopManual()) message('توقّف تحكّمك. عرض الشاشة مستمر.'); }
    else await startManual();
  };
  $('phoneStopScreen').onclick = async () => {
    if (sessionBusy || !device()?.online || !device()?.screen_enabled || (device()?.busy && !owner)) return;
    const id = selected, existing = owner, epoch = ++sessionEpoch;
    let lease = existing;
    sessionBusy = true; render();
    try {
      // This explicit stop action works without loading a phone screenshot first.
      if (!lease) lease = (await request(endpoint(id, 'session'), 'POST', {})).owner;
      if (!lease || typeof lease !== 'string') throw Error('تعذّر تأكيد جلسة الهاتف.');
      if (epoch !== sessionEpoch || document.hidden || id !== selected) return;
      await request(endpoint(id, 'command'), 'POST', {owner: lease, action: 'stop_screen', args: {}});
      stopViewing();
      message('توقّفت مشاركة شاشة الهاتف. السماح بالتحكّم يبقى محفوظًا؛ لإظهار الشاشة مجددًا وافق من تطبيق الهاتف.');
      await refresh();
    } catch (error) { message(error.message, true); }
    finally {
      if (!existing && lease) {
        try { await request(endpoint(id, 'session/stop'), 'POST', {owner: lease}, {keepalive: true}); }
        catch (error) { message(`تعذّر تأكيد إنهاء الجلسة: ${error.message}`, true); }
      }
      sessionBusy = false; render();
    }
  };
  $('phoneDevice').onchange = async () => {
    const next = $('phoneDevice').value;
    switching = true; render(); stopViewing();
    await stopManual(); selected = next;
    switching = false; message(''); render();
  };
  $('phoneRefresh').onclick = refresh;
  document.querySelectorAll('[data-phone-key]').forEach(button => {
    button.onclick = () => command('key', {key: button.dataset.phoneKey});
  });
  $('phoneEnter').onclick = () => command('key', {key: 'enter'});
  $('phoneTextForm').onsubmit = async event => {
    event.preventDefault();
    const input = $('phoneText'), text = input.value;
    if (!text) return;
    if (text.length > 1000) { message('أرسل النص على أجزاء، كل جزء حتى 1000 حرف.', true); return; }
    $('phoneSendText').disabled = true;
    try { if (await command('text', {text}) && input.value === text) { input.value = ''; message('تم إرسال النص للتلفون.'); } }
    finally { render(); }
  };
  $('phoneRunTask').onclick = async () => {
    const prompt = $('phoneTask').value.trim(), id = selected;
    if (!prompt) { message('اكتب شو بدك الإيجنت يعمل على التلفون.'); $('phoneTask').focus(); return; }
    if (taskBusy || !allowed(device())) return;
    taskBusy = true; render();
    try {
      if (!await stopManual()) return;
      const task = await request('/api/tasks', 'POST', {prompt, kind: 'operate', device_id: id, budget: $('budget')?.value || 'auto'});
      if ($('phoneTask').value.trim() === prompt) $('phoneTask').value = '';
      message('بدأت مهمة التلفون. تابع الخطوات والموافقات ضمن تفاصيل المهمة.');
      if (typeof window.openTask === 'function') window.openTask(task.id);
      if (typeof window.loadHistory === 'function') window.loadHistory();
      document.dispatchEvent(new CustomEvent('hassan:phone-task', {detail: {id: task.id, device_id: id}}));
      await refresh();
    } catch (error) { message(error.message, true); }
    finally { taskBusy = false; render(); }
  };
  $('phonePair').onclick = async () => {
    if (!local) return;
    $('phonePair').disabled = true;
    try {
      const data = await request('/api/phones/pair-code', 'POST');
      $('phonePairCode').textContent = data.code;
      $('phoneServerUrl').value = data.server_url || '';
      $('phonePairResult').hidden = false;
      const until = Date.now() + Number(data.expires_in || 0) * 1000;
      clearInterval(pairTimer);
      const tick = () => {
        const seconds = Math.max(0, Math.ceil((until - Date.now()) / 1000));
        $('phonePairExpiry').textContent = seconds ? `الرمز صالح لمدة ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}.` : 'انتهت صلاحية الرمز. اعمل رمز جديد لتربط الهاتف.';
        if (!seconds) { clearInterval(pairTimer); $('phonePairCode').textContent = '—'; }
      };
      tick(); pairTimer = setInterval(tick, 1000);
    } catch (error) { message(error.message, true); }
    finally { render(); }
  };
  function leave() { stopViewing(); stopManual({keepalive: true, quiet: true}); }
  document.addEventListener('visibilitychange', () => { if (document.hidden) leave(); else refresh(); });
  document.addEventListener('hassan:devices-hidden', leave);
  window.addEventListener('pagehide', leave);
  setInterval(refresh, 5000);
  render(); refresh();
})();
