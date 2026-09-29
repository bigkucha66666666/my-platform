const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scriptPath = path.join(__dirname, '../_static/dynamic_bottleneck_round/sync_poll.js');

function render({ready = false, pathname = '/p/A/round/4/ready', random = 0,
                 storage = new Map(), blocked = false, panelId = 'round-start-sync'} = {}) {
    const timers = [];
    let clicks = 0;
    let reloads = 0;
    const panel = {dataset: {ready: ready ? '1' : '0', poll: '1500', delay: '200'}};
    const window = {
        document: {getElementById: id => id === panelId ? panel : {click() {clicks++;}}},
        location: {pathname, reload() {reloads++;}},
        Math: {random: () => random},
        sessionStorage: {
            getItem(key) {if (blocked) throw Error('blocked'); return storage.get(key) ?? null;},
            setItem(key, value) {if (blocked) throw Error('blocked'); storage.set(key, value);},
            removeItem(key) {if (blocked) throw Error('blocked'); storage.delete(key);},
        },
        setTimeout(fn, delay) {timers.push({fn, delay});},
    };
    vm.runInNewContext(fs.readFileSync(scriptPath, 'utf8'), {window, Math});
    window.DynamicBottleneckSync.start(panelId, 'next');
    return {timers, clicks: () => clicks, reloads: () => reloads};
}

test('unready pages poll with bounded backoff across reloads', () => {
    const storage = new Map();
    const delays = [];
    for (let i = 0; i < 25; i++) {
        const page = render({storage, random: 0.5});
        assert.equal(page.timers.length, 1);
        delays.push(page.timers[0].delay);
        page.timers[0].fn();
        assert.equal(page.reloads(), 1);
        assert.equal(page.clicks(), 0);
    }
    assert.equal(delays[0], 1750);
    assert.ok(delays.every(ms => ms >= 1500 && ms <= 4000));
    assert.ok(delays[1] > delays[0]);
    assert.equal(delays.at(-1), 3750);
});

test('random jitter separates clients without exceeding 4 seconds', () => {
    const early = render({random: 0}).timers[0].delay;
    const late = render({random: 0.999}).timers[0].delay;
    assert.equal(early, 1500);
    assert.ok(late > early && late < 2000);
    const storage = new Map();
    for (let i = 0; i < 20; i++) {
        assert.ok(render({storage, random: 0.999}).timers[0].delay <= 4000);
    }
});

test('ready pages keep 200ms transition and clear their retry counter', () => {
    const storage = new Map();
    render({storage});
    const page = render({storage, ready: true});
    assert.equal(page.timers[0].delay, 200);
    page.timers[0].fn();
    assert.equal(page.clicks(), 1);
    assert.equal(page.reloads(), 0);
    assert.equal(storage.size, 0);
    assert.equal(render({storage}).timers[0].delay, 1500);
});

test('rounds, participants and sync page types have independent counters', () => {
    const storage = new Map();
    render({storage});
    assert.ok(render({storage}).timers[0].delay > 1500);
    for (const options of [
        {pathname: '/p/A/round/5/ready'},
        {pathname: '/p/B/round/4/ready'},
        {panelId: 'sync-panel'},
    ]) {
        assert.equal(render({...options, storage}).timers[0].delay, 1500);
    }
});

test('blocked browser storage does not stop polling or ready transition', () => {
    const unready = render({blocked: true});
    assert.equal(unready.timers[0].delay, 1500);
    unready.timers[0].fn();
    assert.equal(unready.reloads(), 1);
    const ready = render({blocked: true, ready: true});
    ready.timers[0].fn();
    assert.equal(ready.clicks(), 1);
});

test('both sync templates load the shared script and start the correct panel', () => {
    for (const [template, panel, button] of [
        ['RoundStartSync.html', 'round-start-sync', 'round-start-next'],
        ['ResultsSync.html', 'sync-panel', 'sync-next'],
    ]) {
        const source = fs.readFileSync(path.join(__dirname, template), 'utf8');
        assert.ok(source.includes("{{ static 'dynamic_bottleneck_round/sync_poll.js' }}"));
        assert.ok(source.includes(`DynamicBottleneckSync.start('${panel}', '${button}')`));
    }
});
