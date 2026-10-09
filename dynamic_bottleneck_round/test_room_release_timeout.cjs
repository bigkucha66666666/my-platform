const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', '_static',
  'dynamic_bottleneck_round', 'room_release.js'), 'utf8');

function harness() {
  let now = 0;
  let nextTimer = 0;
  let formHandler;
  let nativePosts = 0;
  const timers = new Map();
  const calls = [];
  const form = {addEventListener(kind, handler) {
    if (kind === 'submit') formHandler = handler;
  }, submit() { nativePosts++; }};
  const elements = {
    'room-release-gate': {dataset: {condition: 'I0', shellToken: 'a'.repeat(24),
      controlToken: 'signed', controlEndpoint: '/dynamic-room-sync'}},
    'decision-content': {}, 'room-release-message': {},
    'decision-submit': {form, addEventListener() {}},
    'departure-minute': {value: '474'},
  };
  const window = {
    document: {getElementById: id => elements[id]}, Math,
    AbortController,
    performance: {now: () => now},
    crypto: {getRandomValues: values => values.fill(7)},
    setTimeout(fn, delay) {
      const id = ++nextTimer;
      timers.set(id, {fn, at: now + delay, delay});
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      return new Promise((resolve, reject) => calls.push({
        data: JSON.parse(options.body), signal: options.signal, resolve, reject,
      }));
    },
  };
  vm.runInNewContext(script, {window});
  window.DynamicBottleneckRelease.start();
  return {
    calls, elements, window,
    submit() { formHandler({preventDefault() {}}); },
    get nativePosts() { return nativePosts; },
    async advance(milliseconds) {
      const target = now + milliseconds;
      while (true) {
        const due = [...timers].filter(([, timer]) => timer.at <= target)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        now = due[1].at;
        timers.delete(due[0]);
        due[1].fn();
        await new Promise(resolve => setImmediate(resolve));
      }
      now = target;
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const headers = harness();
  headers.submit();
  assert.equal(headers.calls.length, 1);
  assert.equal(headers.calls[0].data.kind, 'ack');
  await headers.advance(12000);
  assert.equal(Boolean(headers.calls[0].signal && headers.calls[0].signal.aborted), true,
    'hanging ACK headers must be aborted within a finite bound');
  assert.ok(headers.calls.some(call => call.data.kind === 'decision'),
    'queued choice must make progress after timeout');
  assert.equal(headers.calls.find(call => call.data.kind === 'decision').data.departure_minute, 474);
  headers.calls[0].resolve({ok: true, json: async () => ({technical_anomaly: 'stale_fault'})});
  await flush();
  assert.equal(headers.calls.length, 2, 'late ACK cannot schedule another request');
  assert.equal(headers.nativePosts, 0);

  const body = harness();
  body.submit();
  let resolveBody;
  body.calls[0].resolve({ok: true, json: () => new Promise(resolve => { resolveBody = resolve; })});
  await flush();
  await body.advance(12000);
  assert.equal(Boolean(body.calls[0].signal && body.calls[0].signal.aborted), true,
    'hanging JSON body must be aborted within a finite bound');
  assert.equal(body.calls[1].data.kind, 'decision');
  resolveBody({technical_anomaly: 'stale_fault', clock_id: body.calls[0].data.clock_id,
    clock_token: body.calls[0].data.clock_token});
  await flush();
  assert.equal(body.calls.length, 2, 'late JSON cannot schedule another request');
  assert.equal(body.nativePosts, 0);

  const probe = harness();
  probe.calls[0].resolve({ok: true, json: async () => ({release_ts: null,
    deadline_ts: null, server_now_ts: 100,
    clock_id: probe.calls[0].data.clock_id, clock_token: probe.calls[0].data.clock_token})});
  await flush();
  probe.submit();
  assert.equal(probe.calls[1].data.kind, 'decision');
  probe.calls[1].resolve({ok: false, status: 400,
    json: async () => ({error: 'control_request_rejected'})});
  await flush();
  assert.equal(probe.calls[2].data.kind, 'status');
  let resolveProbeBody;
  probe.calls[2].resolve({ok: true, json: () => new Promise(resolve => { resolveProbeBody = resolve; })});
  await flush();
  await probe.advance(12000);
  assert.equal(Boolean(probe.calls[2].signal && probe.calls[2].signal.aborted), true,
    'hanging rejection status probe JSON must be aborted');
  assert.equal(probe.calls[3].data.kind, 'decision',
    'probe timeout must release the one active attempt for retry');
  assert.equal(probe.calls[3].data.departure_minute, 474);
  resolveProbeBody({technical_anomaly: 'stale_probe_fault'});
  await flush();
  assert.equal(probe.calls.length, 4);
  assert.equal(probe.nativePosts, 0);
  console.log('room release timeout checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
