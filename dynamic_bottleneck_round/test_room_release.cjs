const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '..', '_static',
  'dynamic_bottleneck_round', 'room_release.js'), 'utf8');
const sandbox = {window: {}, setTimeout, clearTimeout};
vm.runInNewContext(script, sandbox);
const release = sandbox.window.DynamicBottleneckRelease;

const armed = {release_ts: 105, deadline_ts: 165, server_now_ts: 104,
  technical_anomaly: ''};
assert.equal(release.canActivate(armed, 'I0'), false);
assert.equal(release.canActivate(armed, 'I1'), false);
assert.equal(release.canActivate({...armed, server_now_ts: 105}, 'I0'), true);
assert.equal(release.canActivate({...armed, server_now_ts: 105}, 'I1'), false);
assert.equal(release.canActivate({...armed, server_now_ts: 105,
  actual_capacity_display: '3.14'}, 'I1'), true);
assert.equal(release.canActivate({...armed, server_now_ts: 165,
  actual_capacity_display: '3.14'}, 'I1'), false);
assert.equal(release.canActivate({...armed, server_now_ts: 105,
  actual_capacity_display: '3.14', technical_anomaly: 'queue_failure'}, 'I1'), false);
assert.equal(release.connectionStalled(9999), false);
assert.equal(release.connectionStalled(10000), true);

const messages = [];
const token = 'a'.repeat(24);
const elements = {
  'room-release-gate': {dataset: {condition: 'I0', shellToken: token}},
  'decision-content': {},
  'room-release-message': {},
  'decision-submit': {addEventListener() {}},
};
sandbox.window.document = {getElementById: (id) => elements[id]};
sandbox.window.Date = Date;
let normalNowMs = 0;
const normalTimers = [];
sandbox.window.performance = {now: () => normalNowMs};
sandbox.window.crypto = {getRandomValues: values => values.fill(1)};
sandbox.window.Math = Math;
sandbox.window.setTimeout = (fn, delay) => { normalTimers.push({fn, delay}); return normalTimers.length; };
sandbox.window.clearTimeout = () => {};
sandbox.window.liveSend = (data) => messages.push(data);
release.start();
assert.equal(messages[0].kind, 'ack');
assert.equal(messages[0].shell_token, token);
sandbox.window.liveRecv({...armed, server_now_ts: 105, clock_id: messages[0].clock_id,
  clock_token: messages[0].clock_token});
assert.equal(messages[1].kind, 'operative');
assert.equal(messages[1].shell_token, undefined);
normalNowMs = 1000;
sandbox.window.liveRecv({release_ts: null, deadline_ts: null, server_now_ts: 100});
assert.equal(normalTimers.at(-1).delay, 59000);
sandbox.window.liveRecv({...armed, technical_anomaly: 'worker_restarted_during_round'});
assert.equal(elements['decision-content'].hidden, true);
const timersAfterAnomaly = normalTimers.length;
sandbox.window.liveRecv({...armed, server_now_ts: 106});
assert.equal(normalTimers.length, timersAfterAnomaly);

// A server response at 164s arriving 3s later must not reopen deadline 165s.
let monotonicMs = 0;
let autoClicks = 0;
const delayedMessages = [];
const delayedTimers = [];
const delayedElements = {
  'room-release-gate': {dataset: {condition: 'I0', shellToken: token}},
  'decision-content': {hidden: true},
  'room-release-message': {},
  'decision-submit': {addEventListener() {}, click() { autoClicks++; }},
};
const delayedWindow = {
  document: {getElementById: id => delayedElements[id]}, Date, Math,
  performance: {now: () => monotonicMs},
  crypto: {getRandomValues: values => values.fill(2)},
  setTimeout: (fn, delay) => { delayedTimers.push({fn, delay}); return delayedTimers.length; },
  clearTimeout() {}, liveSend: data => delayedMessages.push(data),
};
vm.runInNewContext(script, {window: delayedWindow});
delayedWindow.DynamicBottleneckRelease.start();
// Another tab for the same participant can receive the same oTree broadcast.
delayedWindow.liveRecv({...armed, server_now_ts: 1000,
  clock_id: delayedMessages[0].clock_id, clock_token: 'f'.repeat(32)});
assert.equal(delayedElements['decision-content'].hidden, true);
assert.notEqual(delayedElements['room-release-message'].textContent,
  '本轮时间已结束，正在进入结果页面。');
delayedWindow.liveRecv({...armed, server_now_ts: 100,
  release_ts: null, deadline_ts: null, acknowledged: true,
  clock_id: delayedMessages[0].clock_id, clock_token: delayedMessages[0].clock_token});
monotonicMs = 2000;
delayedTimers.at(-1).fn();
monotonicMs = 4000;
delayedWindow.liveRecv({...armed, server_now_ts: 102,
  clock_id: delayedMessages.at(-1).clock_id, clock_token: delayedMessages.at(-1).clock_token});
// The accurate earlier sample says it is now 104s: do not wait 105-102 again.
assert.equal(delayedTimers.at(-1).delay, 1050);
monotonicMs = 64000;
delayedTimers.at(-1).fn();
monotonicMs = 67000;
delayedWindow.liveRecv({...armed, server_now_ts: 164,
  clock_id: delayedMessages.at(-1).clock_id, clock_token: delayedMessages.at(-1).clock_token});
assert.equal(delayedElements['decision-content'].hidden, true);
assert.match(delayedElements['room-release-message'].textContent, /时间已结束/);
assert.equal(delayedTimers.at(-1).delay, 150);
delayedTimers.at(-1).fn();
assert.equal(autoClicks, 1);

// With 6s clock uncertainty, close controls first but never auto-POST early.
let uncertainNow = 0;
let uncertainClicks = 0;
const uncertainMessages = [];
const uncertainTimers = [];
const uncertainElements = {
  'room-release-gate': {dataset: {condition: 'I0', shellToken: token}},
  'decision-content': {hidden: true},
  'room-release-message': {},
  'decision-submit': {addEventListener() {}, click() { uncertainClicks++; }},
};
const uncertainWindow = {
  document: {getElementById: id => uncertainElements[id]}, Math,
  performance: {now: () => uncertainNow},
  crypto: {getRandomValues: values => values.fill(3)},
  setTimeout: (fn, delay) => { uncertainTimers.push({fn, delay}); return uncertainTimers.length; },
  clearTimeout() {}, liveSend: data => uncertainMessages.push(data),
};
vm.runInNewContext(script, {window: uncertainWindow});
uncertainWindow.DynamicBottleneckRelease.start();
uncertainNow = 6000;
uncertainWindow.liveRecv({release_ts: 100, deadline_ts: 160, server_now_ts: 100,
  clock_id: uncertainMessages[0].clock_id, clock_token: uncertainMessages[0].clock_token});
assert.equal(uncertainElements['decision-content'].hidden, false);
assert.equal(uncertainTimers.at(-1).delay, 54000);
uncertainNow = 60000;
uncertainTimers.at(-1).fn();
assert.equal(uncertainElements['decision-content'].hidden, true);
assert.equal(uncertainClicks, 0);
assert.equal(uncertainTimers.at(-1).delay, 6150);
uncertainTimers.at(-1).fn();
assert.equal(uncertainClicks, 1);
console.log('room release admission and delayed-response clock checks passed');

// Protocol 2 sends decision independently and retains a failed choice for retry.
const v2calls = [], v2timers = [];
let formHandler, nativePosts = 0;
const v2form = {addEventListener(kind, fn) { if (kind === 'submit') formHandler = fn; },
  requestSubmit() { formHandler({preventDefault() {}}); }, submit() { nativePosts++; }};
const v2elements = {
  'room-release-gate': {dataset: {condition: 'I0', shellToken: token,
    controlToken: 'signed', controlEndpoint: '/dynamic-room-sync'}},
  'decision-content': {}, 'room-release-message': {},
  'decision-submit': {form: v2form, addEventListener() {}, click() { v2form.requestSubmit(); }},
  'departure-minute': {value: '474'},
};
const v2window = {document: {getElementById: id => v2elements[id]}, Math,
  performance: {now: () => 0}, crypto: {getRandomValues: a => a.fill(4)},
  AbortController,
  setTimeout(fn, delay) { v2timers.push({fn, delay}); return v2timers.length; }, clearTimeout() {},
  fetch(url, options) { return new Promise((resolve, reject) =>
    v2calls.push({data: JSON.parse(options.body), resolve, reject})); }};
vm.runInNewContext(script, {window: v2window});
v2window.DynamicBottleneckRelease.start();
assert.equal(v2calls.length, 1, 'v2 must send control ACK');
formHandler({preventDefault() {}});
v2elements['departure-minute'].value = '475';
assert.equal(v2calls.length, 2, 'decision starts independently of ACK');
(async () => {
  v2calls[0].resolve({ok: true, json: async () => ({...armed,
    clock_id: v2calls[0].data.clock_id, clock_token: v2calls[0].data.clock_token})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(v2calls[1].data.kind, 'decision');
  assert.equal(v2calls[1].data.departure_minute, 474);
  formHandler({preventDefault() {}});
  v2calls[1].reject(new Error('lost reply'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(nativePosts, 0);
  v2timers.at(-1).fn();
  assert.equal(v2calls[2].data.departure_minute, 474);
  formHandler({preventDefault() {}});
  v2calls[2].resolve({ok: true, json: async () => ({...armed, decision_received: true,
    clock_id: v2calls[2].data.clock_id, clock_token: v2calls[2].data.clock_token})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(nativePosts, 1);
  assert.equal(v2calls.length, 3, 'repeat Enter does not send another native submit');
  // A rejected receipt may mean the round became anomalous. Probe status
  // before retrying so the decision queue cannot starve that notification.
  v2window.DynamicBottleneckRelease.start();
  v2calls[3].resolve({ok: true, json: async () => ({...armed,
    clock_id: v2calls[3].data.clock_id, clock_token: v2calls[3].data.clock_token})});
  await new Promise(resolve => setImmediate(resolve));
  formHandler({preventDefault() {}});
  v2calls[4].resolve({ok: false, status: 400,
    json: async () => ({error: 'control_request_rejected'})});
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(v2calls[5], 'rejected decision probes status');
  assert.equal(v2calls[5].data.kind, 'status');
  v2calls[5].resolve({ok: true, json: async () => ({...armed,
    technical_anomaly: 'decision_control_queue_overrun'})});
  await new Promise(resolve => setImmediate(resolve));
  formHandler({preventDefault() {}});
  assert.equal(v2calls.length, 6, 'technical anomaly stops further controls');
  assert.equal(nativePosts, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
