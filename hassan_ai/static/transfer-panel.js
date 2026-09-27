/* Explicit uploads and clipboard actions; no automatic clipboard access. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('transferCard')) return;
  const MAX_FILE = 25 * 1024 * 1024;
  let uploading = false, clipboardBusy = false, ready = false;
  const status = (id, text, error = false) => { $(id).textContent = text; $(id).dataset.error = String(error); };
  async function request(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'تعذّر إتمام الطلب.');
    return data;
  }
  function controls() {
    $('transferUpload').disabled = uploading;
    $('transferFile').disabled = uploading;
    $('transferClipboardRead').disabled = clipboardBusy || !ready;
    $('transferClipboardWrite').disabled = clipboardBusy || !ready;
  }
  async function refresh() {
    $('transferRefresh').disabled = true;
    try {
      const data = await request('/api/transfers');
      const list = $('transferList'); list.replaceChildren();
      for (const file of data.files || []) {
        if (!/^[0-9a-f]{32}$/.test(file.id)) continue;
        const item = document.createElement('li'), link = document.createElement('a');
        link.textContent = file.name; link.href = `/api/transfers/${file.id}`; link.download = file.name;
        const detail = document.createElement('small');
        detail.textContent = `${(file.size / (1024 * 1024)).toFixed(2)} ميغابايت · تنزيل على هذا الجهاز`;
        const proof = document.createElement('details'), summary = document.createElement('summary'), hash = document.createElement('code');
        summary.textContent = 'بصمة الملف SHA-256'; hash.textContent = file.sha256;
        proof.append(summary, hash); item.append(link, detail, proof); list.append(item);
      }
      if (!list.children.length) { const item = document.createElement('li'); item.textContent = 'ما في ملفات مرفوعة بعد.'; list.append(item); }
    } catch (error) { status('transferStatus', error.message, true); }
    finally { $('transferRefresh').disabled = false; }
  }
  async function refreshClipboard() {
    if (document.hidden) return;
    try {
      const state = await request('/api/desktop');
      ready = Boolean(state.enabled && state.connected);
      $('transferClipboardHint').textContent = ready ? 'النسخ واللصق متاح للجهاز الذي يتحكّم بالكمبيوتر الآن، وبكبسة منك فقط.' : 'ابدأ التحكّم اليدوي بالكمبيوتر أولًا. القراءة والكتابة تحصل فقط بكبسة منك.';
    } catch { ready = false; }
    controls();
  }
  $('transferRefresh').onclick = refresh;
  $('transferUpload').onclick = async () => {
    if (uploading) return;
    const file = $('transferFile').files?.[0];
    if (!file) { status('transferStatus', 'اختار ملف أولًا.'); return; }
    if (file.size > MAX_FILE) { status('transferStatus', 'حجم الملف يجب ألا يتجاوز 25 ميغابايت.', true); return; }
    uploading = true; controls(); status('transferStatus', 'جارٍ رفع الملف…');
    try {
      const result = await request(`/api/transfers?name=${encodeURIComponent(file.name)}`, {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file});
      $('transferFile').value = '';
      status('transferStatus', `تم رفع ${result.name}. افتح هذه الصفحة من جهازك الثاني لتنزيله.`);
      await refresh();
    } catch (error) { status('transferStatus', error.message, true); }
    finally { uploading = false; controls(); }
  };
  async function clipboard(action) {
    if (clipboardBusy || !ready) return;
    const text = $('transferText').value;
    if (action === 'write' && (text.length > 65536 || text.includes('\0'))) { status('transferClipboardStatus', 'النص طويل جدًا أو يحتوي محارف غير صالحة.', true); return; }
    clipboardBusy = true; controls();
    try {
      const data = await request(`/api/clipboard/${action}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(action === 'write' ? {text} : {})});
      if (action === 'read') $('transferText').value = data.text;
      status('transferClipboardStatus', action === 'read' ? 'وصل نص حافظة الكمبيوتر. حدده وانسخه من جهازك.' : 'تم وضع النص في حافظة الكمبيوتر.');
    } catch (error) { status('transferClipboardStatus', error.message, true); }
    finally { clipboardBusy = false; controls(); }
  }
  $('transferClipboardRead').onclick = () => clipboard('read');
  $('transferClipboardWrite').onclick = () => clipboard('write');
  $('transferSelectText').onclick = () => { $('transferText').focus(); $('transferText').select(); };
  $('transferClipboard').addEventListener('toggle', () => { if ($('transferClipboard').open) refreshClipboard(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshClipboard(); });
  refresh(); refreshClipboard();
  setInterval(() => { if ($('transferClipboard').open) refreshClipboard(); }, 3000);
})();
