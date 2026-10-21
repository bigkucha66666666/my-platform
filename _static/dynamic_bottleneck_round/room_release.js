(function (window) {
  'use strict';

  function canActivate(status, condition) {
    return Boolean(status && !status.technical_anomaly && status.release_ts &&
      status.server_now_ts >= status.release_ts &&
      status.server_now_ts < status.deadline_ts &&
      (condition !== 'I1' || status.actual_capacity_display));
  }

  function connectionStalled(millisecondsSinceReply) {
    return millisecondsSinceReply >= 10000;
  }

  function start() {
    const gate = window.document.getElementById('room-release-gate');
    const content = window.document.getElementById('decision-content');
    const message = window.document.getElementById('room-release-message');
    const submit = window.document.getElementById('decision-submit');
    if (!gate || !content || !message || !submit) return;
    const condition = gate.dataset.condition;
    const shellToken = gate.dataset.shellToken;
    const controlEndpoint = gate.dataset.controlEndpoint;
    const controlToken = gate.dataset.controlToken;
    const protocol2 = Boolean(controlEndpoint && controlToken);
    const form = submit.form;
    const lanes = {
      decision: {inFlight: false, retryTimer: undefined},
      other: {inFlight: false, retryTimer: undefined},
    };
    let savedChoice;
    let probeWaiter;
    const controlQueue = new Map();
    let active = false;
    let acknowledged = false;
    let submitted = false;
    let technicalStopped = false;
    let pollTimer;
    let deadlineTimer;
    const monotonicNow = function () { return window.performance.now(); };
    let lastReplyMs = monotonicNow();
    let clockId = 0;
    const clockToken = Array.from(window.crypto.getRandomValues(new Uint32Array(4)),
      value => value.toString(16).padStart(8, '0')).join('');
    const pendingClocks = new Map();
    let lowerOffsetMs = -Infinity;
    let upperOffsetMs = Infinity;
    let decisionWindow;
    const CONTROL_TIMEOUT_MS = 10000;

    async function fetchControl(data, keepalive) {
      const controller = new window.AbortController();
      const id = data.clock_id;
      pendingClocks.set(id, monotonicNow());
      if (pendingClocks.size > 32) pendingClocks.delete(pendingClocks.keys().next().value);
      let timeoutId;
      let completed = false;
      const timeout = new Promise((resolve, reject) => {
        timeoutId = window.setTimeout(function () {
          controller.abort();
          reject(new Error('control timeout'));
        }, CONTROL_TIMEOUT_MS);
      });
      try {
        const response = await Promise.race([window.fetch(controlEndpoint, {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...data, token: controlToken}),
          keepalive: Boolean(keepalive), signal: controller.signal,
        }), timeout]);
        const status = await Promise.race([response.json(), timeout]);
        observeClock(status);
        completed = true;
        return {response, status};
      } finally {
        window.clearTimeout(timeoutId);
        pendingClocks.delete(id);
        if (!completed) controller.abort();
      }
    }

    function observeClock(status) {
      if (status.clock_token !== clockToken) return;
      const receivedMs = monotonicNow();
      const sentMs = pendingClocks.get(status.clock_id);
      if (sentMs === undefined || !Number.isFinite(status.server_now_ts)) return;
      pendingClocks.delete(status.clock_id);
      // Server sampled between send and receive. The interval includes both
      // network and server queue delay, without trusting the browser wall clock.
      lowerOffsetMs = Math.max(lowerOffsetMs, status.server_now_ts * 1000 - receivedMs);
      upperOffsetMs = Math.min(upperOffsetMs, status.server_now_ts * 1000 - sentMs);
    }

    function controlPump() {
      pumpLane('decision');
      pumpLane('other');
    }

    function checkStatus() {
      return new Promise((resolve, reject) => {
        const waiter = {resolve, reject};
        probeWaiter = waiter;
        const timer = window.setTimeout(function () {
          if (probeWaiter === waiter) probeWaiter = undefined;
          reject(new Error('status probe timeout'));
        }, CONTROL_TIMEOUT_MS);
        waiter.resolve = status => {
          window.clearTimeout(timer);
          resolve(status);
        };
        waiter.reject = error => {
          window.clearTimeout(timer);
          reject(error);
        };
        controlQueue.set('status', {kind: 'status'});
        controlPump();
      });
    }

    function pumpLane(name) {
      const lane = lanes[name];
      if (technicalStopped || submitted || lane.inFlight || lane.retryTimer) return;
      const kinds = name === 'decision' ? ['decision'] : ['operative', 'ack', 'status'];
      const kind = kinds.find(k => controlQueue.has(k));
      if (!kind) return;
      const data = controlQueue.get(kind);
      controlQueue.delete(kind);
      data.clock_id = ++clockId;
      data.clock_token = clockToken;
      lane.inFlight = true;
      fetchControl(data, kind === 'decision' || kind === 'operative')
        .then(async ({response, status}) => {
          if (kind === 'status' && probeWaiter) {
            const waiter = probeWaiter;
            probeWaiter = undefined;
            waiter.resolve(status);
          }
          if (status.technical_anomaly) window.liveRecv(status);
          if (!response.ok && response.status === 400 && kind === 'decision' && !technicalStopped) {
            window.liveRecv(await checkStatus());
          }
          if (!response.ok) throw new Error(status.error || 'control rejected');
          window.liveRecv(status);
          if (kind === 'decision') {
            if (!status.decision_received || technicalStopped) throw new Error('decision unconfirmed');
            if (submitted) return;
            submitted = true;
            controlQueue.delete('decision');
            if (deadlineTimer) window.clearTimeout(deadlineTimer);
            // Durable receipt is canonical; native oTree remains responsible
            // for advancing pages and committing business results.
            form.submit();
          }
        }).catch(() => {
          if (kind === 'status' && probeWaiter) {
            const waiter = probeWaiter;
            probeWaiter = undefined;
            waiter.reject(new Error('status probe failed'));
          }
          if (technicalStopped || submitted) return;
          controlQueue.set(kind, data);
          message.textContent = '服务器确认暂未完成，正在重试，请保持页面打开。';
          lane.retryTimer = window.setTimeout(function () {
            lane.retryTimer = undefined;
            controlPump();
          }, 1500);
        }).finally(() => {
          lane.inFlight = false;
          controlPump();
        });
    }

    function send(kind) {
      if (protocol2) {
        if (technicalStopped || submitted) return;
        if (kind === 'decision' && (lanes.decision.inFlight || lanes.decision.retryTimer)) return;
        const data = {kind: kind};
        if (kind === 'decision') data.departure_minute = savedChoice;
        // Status is coalesced, and ACK supersedes a queued status.
        if (kind === 'ack') controlQueue.delete('status');
        controlQueue.set(kind, data);
        controlPump();
        return;
      }
      if (typeof window.liveSend === 'function') {
        clockId++;
        pendingClocks.set(clockId, monotonicNow());
        if (pendingClocks.size > 32) pendingClocks.delete(pendingClocks.keys().next().value);
        const data = {kind: kind, clock_id: clockId, clock_token: clockToken};
        if (kind === 'ack') data.shell_token = shellToken;
        window.liveSend(data);
      }
    }

    function schedulePoll(delayMs) {
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(function () {
        if (!active && !submitted &&
            connectionStalled(monotonicNow() - lastReplyMs)) {
          message.textContent = '与服务器的确认连接暂时无响应。请保持页面打开，并联系实验管理员。';
        }
        if (!active && !submitted) send(acknowledged ? 'status' : 'ack');
        // A lost WebSocket response must not strand the page forever.
        if (!active && !submitted) schedulePoll(1800);
      }, delayMs);
    }

    function showCapacity(status) {
      if (condition !== 'I1') return;
      const panel = content.querySelector('.capacity-panel');
      if (!panel) return;
      panel.classList.add('is-known');
      const kicker = panel.querySelector('.capacity-kicker');
      const note = panel.querySelector('.capacity-note');
      const strip = panel.querySelector('.state-strip');
      if (kicker) kicker.textContent = '本轮瓶颈服务率';
      if (note) note.textContent = status.actual_capacity_display +
        ' 人/分钟 · 通行能力等级：' + status.capacity_level_label;
      if (strip) strip.hidden = true;
    }

    function autoAdvance() {
      if (technicalStopped) return;
      if (!submitted) submit.click();
    }

    function closeWindow(status) {
      if (submitted) return;
      content.hidden = true;
      gate.hidden = false;
      message.textContent = '本轮时间已结束，正在进入结果页面。';
      if (pollTimer) window.clearTimeout(pollTimer);
      if (deadlineTimer) window.clearTimeout(deadlineTimer);
      // Close controls using the upper bound, but wait for the lower bound
      // before auto-POST so a default selection cannot be recorded as manual.
      const remaining = Math.max(0,
        status.deadline_ts * 1000 - (monotonicNow() + lowerOffsetMs));
      deadlineTimer = window.setTimeout(autoAdvance, remaining + 150);
    }

    function scheduleDeadline(status) {
      if (submitted) return;
      if (deadlineTimer) window.clearTimeout(deadlineTimer);
      const remaining = Math.max(0,
        status.deadline_ts * 1000 - (monotonicNow() + upperOffsetMs));
      deadlineTimer = window.setTimeout(function () { closeWindow(status); }, remaining);
    }

    function activate(status) {
      if (active) return;
      active = true;
      showCapacity(status);
      gate.hidden = true;
      content.hidden = false;
      send('operative');
      scheduleDeadline(status);
      if (pollTimer) window.clearTimeout(pollTimer);
    }

    if (protocol2 && form) {
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        if (technicalStopped || submitted) return;
        if (savedChoice === undefined) savedChoice = Number(
          window.document.getElementById('departure-minute').value);
        send('decision');
      });
    } else {
      submit.addEventListener('click', function () {
        submitted = true;
        if (deadlineTimer) window.clearTimeout(deadlineTimer);
      });
    }

    window.liveRecv = function (status) {
      if (technicalStopped || !status || typeof status !== 'object') return;
      lastReplyMs = monotonicNow();
      observeClock(status);
      acknowledged = acknowledged || Boolean(status.acknowledged);
      if (!decisionWindow && status.release_ts && status.deadline_ts) {
        decisionWindow = {release_ts: status.release_ts, deadline_ts: status.deadline_ts};
      }
      if (status.technical_anomaly) {
        technicalStopped = true;
        if (probeWaiter) {
          probeWaiter.reject(new Error('technical anomaly'));
          probeWaiter = undefined;
        }
        content.hidden = true;
        gate.hidden = false;
        message.textContent = '本轮出现技术异常，请联系实验管理员。';
        if (pollTimer) window.clearTimeout(pollTimer);
        if (deadlineTimer) window.clearTimeout(deadlineTimer);
        Object.values(lanes).forEach(lane => {
          if (lane.retryTimer) window.clearTimeout(lane.retryTimer);
        });
        controlQueue.clear();
        return;
      }
      if (!Number.isFinite(upperOffsetMs)) {
        schedulePoll(1800);
        return;
      }
      const currentServerUpper = (monotonicNow() + upperOffsetMs) / 1000;
      if (decisionWindow && currentServerUpper >= decisionWindow.deadline_ts) {
        closeWindow(decisionWindow);
        return;
      }
      if (active) {
        scheduleDeadline(decisionWindow);
        return;
      }
      if (canActivate({...status, server_now_ts: currentServerUpper}, condition) &&
          status.server_now_ts >= status.release_ts) {
        activate(status);
        return;
      }
      if (status.release_ts) {
        const duration = Math.round(status.deadline_ts - status.release_ts);
        message.textContent = `本房间已安排统一放行，即将开始 ${duration} 秒决策。`;
        // Use the calibrated current lower bound, not the response's old
        // timestamp. Send no earlier than T, without waiting reply age twice.
        const currentServerLower = (monotonicNow() + lowerOffsetMs) / 1000;
        schedulePoll(Math.max(80,
          (status.release_ts - currentServerLower) * 1000 + 50));
      } else {
        schedulePoll(1500 + Math.floor(window.Math.random() * 500));
      }
    };

    send('ack');
    schedulePoll(1600);
  }

  window.DynamicBottleneckRelease = {
    canActivate: canActivate, connectionStalled: connectionStalled, start: start
  };
})(window);
