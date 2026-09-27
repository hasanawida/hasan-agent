/* Run with node --test tests/test_phone_control.cjs; no phone or OS input. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../hassan_ai/static/phone-control.js'), 'utf8');
const panel = fs.readFileSync(path.join(__dirname, '../hassan_ai/static/phone-panel.html'), 'utf8');
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const json = value => ({ok: true, json: async () => value});
function setup({devices, route} = {}) {
  const calls = [], timers = new Map(), listeners = new Map(), revoked = [], taskIds = [];
  let timer = 0, url = 0;
  class Element {
    constructor(id) { this.id = id; this.hidden = false; this.disabled = false; this.dataset = {}; this.value = ''; this.textContent = ''; this.handlers = new Map(); this.naturalWidth = 200; this.naturalHeight = 400; this.captures = new Set(); }
    addEventListener(name, callback) { this.handlers.set(name, callback); }
    dispatch(name, event = {}) { return this.handlers.get(name)?.({isPrimary: true, button: 0, pointerId: 1, preventDefault() {}, ...event}); }
    replaceChildren() { this.options = []; }
    add(option) { (this.options ||= []).push(option); }
    removeAttribute(name) { delete this[name]; }
    getBoundingClientRect() { return {left: 0, top: 0, width: 400, height: 400}; }
    setPointerCapture(id) { this.captures.add(id); }
    hasPointerCapture(id) { return this.captures.has(id); }
    releasePointerCapture(id) { this.captures.delete(id); this.dispatch('lostpointercapture'); }
    focus() {}
    set src(value) { this._src = value; queueMicrotask(() => this.dispatch('load')); }
    get src() { return this._src; }
  }
  const elements = Object.fromEntries([...panel.matchAll(/\bid="([^"]+)"/g)].map(m => [m[1], new Element(m[1])]));
  const keys = ['back', 'home', 'recents'].map(key => { const e = new Element(); e.dataset.phoneKey = key; return e; });
  const activeDevices = devices || [{device_id: 'phone-1', label: 'My Android', online: true, screen_enabled: true, control_enabled: true, busy: false}];
  const document = {
    hidden: false,
    getElementById: id => elements[id] || null,
    querySelectorAll: () => keys,
    addEventListener: (name, callback) => listeners.set(name, callback),
    dispatchEvent() {},
  };
  const window = {addEventListener: (name, callback) => listeners.set(name, callback), openTask: id => taskIds.push(id), loadHistory() {}};
  const fetch = async (url, options) => {
    const call = {url, options, body: options.body ? JSON.parse(options.body) : undefined}; calls.push(call);
    const special = route?.(call); if (special !== undefined) return await special;
    if (url === '/api/phones') return json({devices: activeDevices, local: true});
    if (url.endsWith('/session')) return json({owner: 'manual-test-lease'});
    if (url.endsWith('/frame')) return {ok: true, headers: {get: () => 'image/jpeg'}, blob: async () => ({size: 512})};
    if (url === '/api/tasks') return json({id: 'task-1'});
    return json({ok: true});
  };
  vm.runInNewContext(source, {document, window, fetch, Option: class {constructor(text, value) {this.text = text; this.value = value;}}, URL: {createObjectURL: () => `blob:${++url}`, revokeObjectURL: value => revoked.push(value)}, AbortController, CustomEvent: class {}, Date, Math, Error,
    setTimeout: (callback, ms) => { const id = ++timer; timers.set(id, {callback, ms}); return id; },
    clearTimeout: id => timers.delete(id), setInterval: (callback, ms) => { const id = ++timer; timers.set(id, {callback, ms}); return id; }, clearInterval: id => timers.delete(id)});
  return {calls, elements, keys, timers, listeners, revoked, taskIds, document};
}
const matching = (app, suffix) => app.calls.filter(c => c.url.endsWith(suffix));

test('page load only discovers phones, and disconnected phones cannot start sessions', async () => {
  const app = setup({devices: [{device_id: 'offline', online: false, control_enabled: true, screen_enabled: true}]});
  await settle();
  assert.deepEqual(app.calls.map(c => c.url), ['/api/phones']);
  assert.equal(app.elements.phoneView.disabled, true);
  assert.equal(app.elements.phoneControl.disabled, true);
  await app.elements.phoneControl.onclick();
  assert.equal(matching(app, '/session').length, 0);
});

test('manual view rejects letterbox taps and sends normalized taps and swipes', async () => {
  const app = setup(); await settle(); await app.elements.phoneControl.onclick(); await settle();
  const screen = app.elements.phoneScreen;
  screen.dispatch('pointerdown', {clientX: 50, clientY: 200});
  screen.dispatch('pointerup', {clientX: 50, clientY: 200}); await settle();
  assert.equal(matching(app, '/command').length, 0);
  screen.dispatch('pointerdown', {clientX: 200, clientY: 200});
  screen.dispatch('pointerup', {clientX: 200, clientY: 200}); await settle();
  assert.deepEqual(matching(app, '/command')[0].body, {owner: 'manual-test-lease', action: 'tap', args: {x: 0.5, y: 0.5}});
  screen.dispatch('pointerdown', {clientX: 200, clientY: 300});
  screen.dispatch('pointerup', {clientX: 200, clientY: 100}); await settle();
  const swipe = matching(app, '/command')[1].body;
  assert.equal(swipe.action, 'swipe'); assert.equal(swipe.args.y1, 0.75); assert.equal(swipe.args.y2, 0.25);
  assert.equal(swipe.args.duration_ms, 100);
});

test('cancelled pointer gestures send no phone input', async () => {
  const app = setup(); await settle(); await app.elements.phoneControl.onclick(); await settle();
  app.elements.phoneScreen.dispatch('pointerdown', {clientX: 200, clientY: 200});
  app.elements.phoneScreen.dispatch('pointercancel');
  app.elements.phoneScreen.dispatch('pointerup', {clientX: 200, clientY: 100}); await settle();
  assert.equal(matching(app, '/command').length, 0);
});

test('hiding releases manual owner, clears image and never automatically reconnects', async () => {
  const app = setup(); await settle(); await app.elements.phoneControl.onclick(); await settle();
  assert.equal(matching(app, '/frame').length, 1);
  assert.equal(app.elements.phoneManual.hidden, false);
  app.document.hidden = true; app.listeners.get('visibilitychange')(); await settle();
  assert.equal(app.elements.phoneManual.hidden, true);
  assert.equal(app.elements.phoneScreenWrap.hidden, true);
  assert.equal(matching(app, '/session/stop').length, 1);
  assert.equal(app.revoked.length, 1);
  app.document.hidden = false; app.listeners.get('visibilitychange')(); await settle();
  assert.equal(matching(app, '/session').length, 1);
  assert.equal(matching(app, '/frame').length, 1);
});

test('a lease acquired after hiding is immediately released', async () => {
  const pending = deferred();
  const app = setup({route: call => call.url.endsWith('/session') ? pending.promise : undefined});
  await settle(); const starting = app.elements.phoneControl.onclick(); await settle();
  app.document.hidden = true; app.listeners.get('visibilitychange')(); await settle();
  pending.resolve(json({owner: 'late-owner'})); await starting; await settle();
  assert.equal(matching(app, '/frame').length, 0);
  assert.deepEqual(matching(app, '/session/stop')[0].body, {owner: 'late-owner'});
  assert.equal(app.elements.phoneManual.hidden, true);
});

test('agent task waits for manual release and includes the selected device', async () => {
  const pending = deferred();
  const app = setup({route: call => call.url.endsWith('/session/stop') ? pending.promise : undefined});
  await settle(); await app.elements.phoneControl.onclick(); await settle();
  app.elements.phoneTask.value = 'افتح إعدادات التخزين';
  const submitting = app.elements.phoneRunTask.onclick(); await settle();
  assert.equal(matching(app, '/api/tasks').length, 0);
  pending.resolve(json({ok: true})); await submitting; await settle();
  assert.deepEqual(matching(app, '/api/tasks')[0].body, {prompt: 'افتح إعدادات التخزين', kind: 'operate', device_id: 'phone-1', budget: 'auto'});
  assert.deepEqual(app.taskIds, ['task-1']);
});

test('Arabic text is sent once only through the explicit text form', async () => {
  const app = setup(); await settle(); await app.elements.phoneControl.onclick(); await settle();
  app.elements.phoneText.value = 'مرحبا من الكمبيوتر';
  await app.elements.phoneTextForm.onsubmit({preventDefault() {}}); await settle();
  assert.deepEqual(matching(app, '/command')[0].body, {owner: 'manual-test-lease', action: 'text', args: {text: 'مرحبا من الكمبيوتر'}});
  assert.equal(app.elements.phoneText.value, '');
});

test('agent can work with persistent control while screen projection is stopped', async () => {
  const app = setup({devices: [{device_id: 'phone-1', online: true, control_enabled: true, screen_enabled: false, busy: false}]});
  await settle();
  assert.equal(app.elements.phoneRunTask.disabled, false);
  assert.equal(app.elements.phoneView.disabled, true);
  app.elements.phoneTask.value = 'اقرأ الإعدادات الظاهرة';
  await app.elements.phoneRunTask.onclick();
  assert.equal(matching(app, '/api/tasks').length, 1);
  assert.equal(matching(app, '/frame').length, 0);
});

test('hiding the local view preserves the manual session and disables screen input', async () => {
  const app = setup(); await settle(); await app.elements.phoneControl.onclick(); await settle();
  await app.elements.phoneView.onclick(); await settle();
  assert.equal(matching(app, '/session/stop').length, 0);
  assert.equal(app.elements.phoneManual.hidden, false);
  assert.equal(app.elements.phoneSendText.disabled, true);
  await app.keys[0].onclick(); await settle();
  assert.equal(matching(app, '/command').length, 0);
});

test('explicit projection stop takes and releases a temporary lease without viewing', async () => {
  const app = setup(); await settle(); await app.elements.phoneStopScreen.onclick(); await settle();
  assert.equal(matching(app, '/frame').length, 0);
  assert.equal(matching(app, '/session').length, 1);
  assert.deepEqual(matching(app, '/command')[0].body, {owner: 'manual-test-lease', action: 'stop_screen', args: {}});
  assert.equal(matching(app, '/session/stop').length, 1);
});

test('a busy agent cannot have its phone lease stolen by the stop screen button', async () => {
  const app = setup({devices: [{device_id: 'phone-1', online: true, control_enabled: true, screen_enabled: true, busy: true, owner_kind: 'agent'}]});
  await settle();
  assert.equal(app.elements.phoneStopScreen.disabled, true);
  await app.elements.phoneStopScreen.onclick(); await settle();
  assert.equal(matching(app, '/session').length, 0);
  assert.equal(matching(app, '/command').length, 0);
});

test('text longer than the shared 1000-character limit never reaches the phone', async () => {
  const app = setup(); await settle(); await app.elements.phoneControl.onclick(); await settle();
  app.elements.phoneText.value = 'س'.repeat(1001);
  await app.elements.phoneTextForm.onsubmit({preventDefault() {}});
  assert.equal(matching(app, '/command').length, 0);
  assert.equal(app.elements.phoneText.value.length, 1001);
});

test('switching away from the devices view releases phone input and stops frame fetching', async () => {
  const app = setup(); await settle(); await app.elements.phoneControl.onclick(); await settle();
  assert.equal(app.elements.phoneScreenWrap.hidden, false);
  app.listeners.get('hassan:devices-hidden')(); await settle();
  assert.equal(app.elements.phoneScreenWrap.hidden, true);
  assert.equal(app.elements.phoneManual.hidden, true);
  assert.equal(matching(app, '/session/stop').length, 1);
  assert.equal(matching(app, '/command').length, 0);
});
