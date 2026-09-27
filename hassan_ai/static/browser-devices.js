/* Per-browser credentials. Never read, render, or store authentication tokens. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const card = $('browserDevicesCard');
  if (!card) return;
  let devices = [], local = false, current = null, busy = false, refreshing = false, timer = null, revision = 0;
  const message = (text, error = false) => {
    $('browserDevicesMessage').textContent = text;
    $('browserDevicesMessage').dataset.error = String(error);
  };
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  async function request(path, method = 'GET', body) {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 10000);
    const options = {method, credentials: 'same-origin', cache: 'no-store', signal: abort.signal};
    if (body !== undefined) { options.headers = {'Content-Type': 'application/json'}; options.body = JSON.stringify(body); }
    try {
      const response = await fetch(path, options);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) throw Error('انتهى دخول هذا المتصفح. حدّث الصفحة واربطه مجددًا من الكمبيوتر.');
        if (response.status === 403) throw Error('هذه الخطوة متاحة من صفحة الكمبيوتر نفسه فقط.');
        if (response.status === 503) throw Error('تعذّر حفظ تغيير الدخول. لم نؤكد نجاحه؛ جرّب من الكمبيوتر بعد معالجة مشكلة الحفظ.');
        throw Error('تعذّر تحديث الجهاز. حدّث القائمة وجرّب مجددًا.');
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw Error('الاتصال بطيء؛ لم نقدر نتأكد من نتيجة الطلب. حدّث القائمة.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function lastSeen(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return 'غير معروف';
    const date = new Date(value * 1000);
    return Number.isNaN(date.getTime()) ? 'غير معروف' : date.toLocaleString('ar', {dateStyle: 'short', timeStyle: 'short'});
  }
  function render() {
    const list = $('browserDevicesList');
    list.replaceChildren();
    $('browserDevicesRefresh').disabled = busy || refreshing;
    const visible = local ? devices : devices.filter(device => device.id === current);
    $('browserDevicesHelp').textContent = local
      ? 'سمّي كل جهاز، أو ألغِ دخوله وحده. هذه القائمة للمتصفحات؛ تطبيقات الهاتف لها قائمة منفصلة.'
      : 'هذا دخول متصفحك الحالي. سمّه باسم تعرفه؛ إدارة بقية الأجهزة من صفحة الكمبيوتر.';
    $('browserDevicesLegacy').hidden = !visible.some(device => device.legacy_migrated === true);
    if (!visible.length) {
      list.append(node('p', 'browser-devices-empty', local
        ? 'ما في متصفحات مربوطة بعد. اربط هاتفك برمز الدخول من الكمبيوتر.'
        : 'ما ظهر دخول مستقل لهذا المتصفح بعد. حدّث الصفحة بعد تسجيل الدخول.'));
      return;
    }
    for (const device of visible) {
      const item = node('article', 'browser-device');
      const heading = node('div', 'browser-device-heading');
      heading.append(node('strong', '', device.label || 'متصفح'));
      if (device.id === current) heading.append(node('span', 'browser-device-current', 'هذا المتصفح'));
      item.append(heading, node('p', 'browser-device-seen', `آخر استخدام: ${lastSeen(device.last_seen)}`));
      const form = node('form', 'browser-device-form');
      const input = node('input'); input.type = 'text'; input.maxLength = 80; input.required = true;
      input.value = device.label || ''; input.autocomplete = 'off'; input.setAttribute('aria-label', 'اسم الجهاز');
      const save = node('button', 'ghost', 'حفظ الاسم'); save.type = 'submit';
      input.disabled = save.disabled = busy;
      form.append(input, save);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return;
        const label = input.value.trim();
        if (!label || label.length > 80) { message('اكتب اسمًا من حرف إلى ٨٠ حرفًا.', true); input.focus(); return; }
        busy = true; revision++; render();
        try {
          const result = await request('/api/devices/rename', 'POST', {id: device.id, label});
          devices = devices.map(item => item.id === device.id ? result.device : item);
          message('تم حفظ اسم الجهاز.');
        } catch (error) { message(error.message, true); }
        finally { busy = false; render(); }
      });
      item.append(form);
      if (local) {
        const revoke = node('button', 'ghost browser-device-revoke', 'إلغاء دخول هذا الجهاز');
        revoke.type = 'button'; revoke.disabled = busy;
        revoke.setAttribute('aria-label', `إلغاء دخول ${device.label || 'هذا الجهاز'}`);
        revoke.addEventListener('click', async () => {
          if (busy) return;
          busy = true; revision++; render();
          try {
            await request(`/api/devices/${encodeURIComponent(device.id)}/revoke`, 'POST');
            devices = devices.filter(item => item.id !== device.id);
            if (current === device.id) current = null;
            message(device.legacy_migrated
              ? 'أُلغي دخوله الحالي. إذا كان يحتفظ بالمفتاح القديم، غيّر مفتاح الدخول لإبطال ذلك المفتاح أيضًا.'
              : 'أُلغي دخول الجهاز. يحتاج رمز ربط جديد ليدخل مرة ثانية.');
          } catch (error) { message(error.message, true); }
          finally { busy = false; render(); }
        });
        item.append(revoke);
      }
      list.append(item);
    }
  }
  async function refresh(force = false) {
    if (refreshing || (!force && (busy || document.hidden || (card.contains(document.activeElement) && document.activeElement?.tagName === 'INPUT')))) return;
    refreshing = true; const requestedRevision = revision; $('browserDevicesRefresh').disabled = true;
    try {
      const data = await request('/api/devices');
      if (requestedRevision !== revision) return;
      devices = Array.isArray(data.devices) ? data.devices.filter(device => /^browser_[0-9a-f]{24}$/.test(device.id)) : [];
      local = data.local === true; current = typeof data.current_id === 'string' ? data.current_id : null;
      render();
    } catch (error) { message(error.message, true); }
    finally { refreshing = false; $('browserDevicesRefresh').disabled = busy; }
  }
  $('browserDevicesRefresh').addEventListener('click', () => refresh(true));
  function startRefresh() { clearInterval(timer); timer = setInterval(() => refresh(), 30000); refresh(); }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('pagehide', () => { clearInterval(timer); timer = null; });
  window.addEventListener('pageshow', startRefresh);
  startRefresh();
})();
