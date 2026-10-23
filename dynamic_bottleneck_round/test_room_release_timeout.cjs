const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', '_static',
  'dynamic_bottleneck_round', 'room_release.js'), 'utf8');

function harness(condition = 'I0') {
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
    'room-release-gate': {dataset: {condition, shellToken: 'a'.repeat(24),
      controlToken: 'signed', controlEndpoint: '/dynamic-room-sync'}},
    'decision-content': {hidden: true, querySelector() { return null; }}, 'room-release-message': {},
    'decision-submit': {form, addEventListener() {},
      click() { formHandler({preventDefault() {}}); }},
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
    get pendingTimers() { return timers.size; },
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
  for (const condition of ['I0', 'I1']) {
    const h = harness(condition);
    const ack = h.calls[0];
    ack.resolve({ok: true, json: async () => ({acknowledged: true,
      release_ts: 103, deadline_ts: 133, server_now_ts: 100,
      clock_id: ack.data.clock_id, clock_token: ack.data.clock_token})});
    await flush();
    assert.match(h.elements['room-release-message'].textContent, /3 秒后/);
    await h.advance(1000);
    assert.match(h.elements['room-release-message'].textContent, /2 秒后/);
    assert.equal(h.elements['decision-content'].hidden, true);
    await h.advance(1000);
    assert.match(h.elements['room-release-message'].textContent, /1 秒后/);
    await h.advance(1000);
    assert.equal(h.elements['decision-content'].hidden, true,
      'countdown reaching T cannot authorize decisions locally');
    assert.match(h.elements['room-release-message'].textContent, /正在确认/);
    await h.advance(51);
    const status = h.calls.at(-1);
    assert.equal(status.data.kind, 'status');
    status.resolve({ok: true, json: async () => ({acknowledged: true,
      release_ts: 103, deadline_ts: 133, server_now_ts: 103.051,
      clock_id: status.data.clock_id, clock_token: status.data.clock_token})});
    await flush();
    if (condition === 'I1') {
      assert.equal(h.elements['decision-content'].hidden, true,
        'I1 still waits for server-released capacity');
      h.window.liveRecv({release_ts: 103, deadline_ts: 133,
        server_now_ts: 103.051, actual_capacity_display: '2.67', capacity_level_label: 'medium'});
    }
    assert.equal(h.elements['decision-content'].hidden, false);
  }
  const stoppedCountdown = harness();
  const countdownAck = stoppedCountdown.calls[0];
  countdownAck.resolve({ok: true, json: async () => ({acknowledged: true,
    release_ts: 103, deadline_ts: 133, server_now_ts: 100,
    clock_id: countdownAck.data.clock_id, clock_token: countdownAck.data.clock_token})});
  await flush();
  await stoppedCountdown.advance(1000);
  stoppedCountdown.window.liveRecv({technical_anomaly: 'test_fault'});
  await stoppedCountdown.advance(5000);
  assert.match(stoppedCountdown.elements['room-release-message'].textContent, /技术异常/);
  assert.equal(stoppedCountdown.elements['decision-content'].hidden, true);

  for (const seconds of [40, 30]) {
    const h = harness();
    const ack = h.calls[0];
    ack.resolve({ok: true, json: async () => ({acknowledged: true,
      release_ts: 105, deadline_ts: 105 + seconds, server_now_ts: 100,
      clock_id: ack.data.clock_id, clock_token: ack.data.clock_token})});
    await flush();
    assert.ok(h.elements['room-release-message'].textContent.includes(`${seconds} 秒决策`));
    await h.advance(5000);
    h.window.liveRecv({acknowledged: true, release_ts: 105,
      deadline_ts: 105 + seconds, server_now_ts: 105});
    assert.equal(h.elements['decision-content'].hidden, false);
    await h.advance(seconds * 1000 - 1);
    assert.equal(h.nativePosts, 0);
    await h.advance(1);
    assert.equal(h.elements['decision-content'].hidden, true,
      `${seconds}s window closes at the server deadline`);
    await h.advance(150);
    const timedOut = h.calls.find(call => call.data.kind === 'decision');
    assert.ok(timedOut, 'auto advance submits to server after cutoff');
    assert.equal(h.nativePosts, 0, 'native POST still waits for durable confirmation');
    timedOut.resolve({ok: true, json: async () => ({decision_received: true,
      decision_kind: 'late'})});
    await flush();
    assert.equal(h.nativePosts, 1, 'one native POST after server confirmation');
  }
  for (const mode of ['headers', 'body', 'backoff']) {
    const h = harness();
    const ack = h.calls[0];
    ack.resolve({ok: true, json: async () => ({acknowledged: true,
      release_ts: 100, deadline_ts: 160, server_now_ts: 100,
      clock_id: ack.data.clock_id, clock_token: ack.data.clock_token})});
    await flush();
    await h.advance(59000);
    const operative = h.calls.filter(call => call.data.kind === 'operative').at(-1);
    assert.ok(operative, 'activated page sends operative');
    if (mode === 'body') {
      operative.resolve({ok: true, json: () => new Promise(() => {})});
      await flush();
    } else if (mode === 'backoff') {
      operative.reject(new Error('offline'));
      await flush();
    }
    h.submit();
    const decision = h.calls.at(-1);
    assert.equal(decision.data.kind, 'decision', `${mode}: second 59 starts decision immediately`);
    assert.equal(decision.data.departure_minute, 474);
    const count = h.calls.length;
    h.elements['departure-minute'].value = '510';
    h.submit();
    assert.equal(h.calls.length, count, 'one decision in flight');
    decision.reject(new Error('offline'));
    await flush();
    h.submit();
    assert.equal(h.calls.length, count, 'manual retry respects decision backoff');
    await h.advance(1500);
    const retry = h.calls.filter(call => call.data.kind === 'decision').at(-1);
    assert.equal(retry.data.departure_minute, 474, 'first choice survives retries and auto advance');
    retry.resolve({ok: true, json: async () => ({decision_received: true})});
    await flush();
    h.submit();
    assert.equal(h.nativePosts, 1, 'durable confirmation advances once');
  }

  const headers = harness();
  headers.submit();
  assert.equal(headers.calls.length, 2, 'decision starts while ACK headers are pending');
  assert.equal(headers.calls[0].data.kind, 'ack');
  assert.equal(headers.calls[1].data.kind, 'decision');
  await headers.advance(12000);
  assert.equal(Boolean(headers.calls[0].signal && headers.calls[0].signal.aborted), true,
    'hanging ACK headers must be aborted within a finite bound');
  assert.ok(headers.calls.some(call => call.data.kind === 'decision'),
    'queued choice must make progress after timeout');
  assert.equal(headers.calls.find(call => call.data.kind === 'decision').data.departure_minute, 474);
  const headerCount = headers.calls.length;
  headers.calls[0].resolve({ok: true, json: async () => ({technical_anomaly: 'stale_fault'})});
  await flush();
  assert.equal(headers.calls.length, headerCount, 'late ACK cannot schedule another request');
  assert.equal(headers.nativePosts, 0);

  const blockedProbe = harness();
  blockedProbe.submit();
  blockedProbe.calls.find(call => call.data.kind === 'decision').resolve({ok: false,
    status: 400, json: async () => ({error: 'control_request_rejected'})});
  await flush();
  assert.equal(blockedProbe.calls.length, 2, 'probe waits behind ACK without a third fetch');
  await blockedProbe.advance(12000);
  assert.equal(blockedProbe.calls.filter(call => call.data.kind === 'decision').length, 2,
    'probe queued behind hanging ACK still releases decision for bounded retry');

  const stopped = harness();
  stopped.submit();
  stopped.window.liveRecv({technical_anomaly: 'fault'});
  await stopped.advance(30000);
  stopped.submit();
  assert.equal(stopped.calls.length, 2, 'technical anomaly stops both lanes including retries');
  assert.equal(stopped.nativePosts, 0);
  assert.ok(stopped.calls.every(call => call.signal.aborted), 'pending fetches remain bounded');

  const stoppedRejection = harness();
  stoppedRejection.submit();
  stoppedRejection.window.liveRecv({technical_anomaly: 'fault'});
  stoppedRejection.calls.find(call => call.data.kind === 'decision').resolve({ok: false,
    status: 400, json: async () => ({error: 'control_request_rejected'})});
  await flush();
  assert.equal(stoppedRejection.pendingTimers, 1,
    'late rejection after stop must not create a diagnostic waiter; only the existing ACK timeout remains');
  assert.equal(stoppedRejection.calls.length, 2);
  assert.equal(stoppedRejection.nativePosts, 0);

  const body = harness();
  body.submit();
  assert.equal(body.calls.find(call => call.data.kind === 'decision').data.departure_minute, 474);
  let resolveBody;
  body.calls[0].resolve({ok: true, json: () => new Promise(resolve => { resolveBody = resolve; })});
  await flush();
  await body.advance(12000);
  assert.equal(Boolean(body.calls[0].signal && body.calls[0].signal.aborted), true,
    'hanging JSON body must be aborted within a finite bound');
  assert.equal(body.calls[1].data.kind, 'decision');
  const bodyCount = body.calls.length;
  resolveBody({technical_anomaly: 'stale_fault', clock_id: body.calls[0].data.clock_id,
    clock_token: body.calls[0].data.clock_token});
  await flush();
  assert.equal(body.calls.length, bodyCount, 'late JSON cannot schedule another request');
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
  const retriedDecision = probe.calls.filter(call => call.data.kind === 'decision')[1];
  assert.ok(retriedDecision,
    'probe timeout must release the one active attempt for retry');
  assert.equal(retriedDecision.data.departure_minute, 474);
  const probeCount = probe.calls.length;
  resolveProbeBody({technical_anomaly: 'stale_probe_fault'});
  await flush();
  assert.equal(probe.calls.length, probeCount);
  assert.equal(probe.nativePosts, 0);
  console.log('room release timeout checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
