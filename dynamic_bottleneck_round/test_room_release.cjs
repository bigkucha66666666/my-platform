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
sandbox.window.Math = Math;
sandbox.window.setTimeout = () => 1;
sandbox.window.clearTimeout = () => {};
sandbox.window.liveSend = (data) => messages.push(data);
release.start();
assert.equal(messages[0].kind, 'ack');
assert.equal(messages[0].shell_token, token);
sandbox.window.liveRecv({...armed, server_now_ts: 105});
assert.equal(messages[1].kind, 'operative');
assert.equal(messages[1].shell_token, undefined);
console.log('room release admission UI checks passed');
