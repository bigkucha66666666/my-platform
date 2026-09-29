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
    let active = false;
    let acknowledged = false;
    let submitted = false;
    let pollTimer;
    let deadlineTimer;
    let lastReplyMs = window.Date.now();

    function send(kind) {
      if (typeof window.liveSend === 'function') {
        window.liveSend(kind === 'ack' ?
          {kind: kind, shell_token: shellToken} : {kind: kind});
      }
    }

    function schedulePoll(delayMs) {
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(function () {
        if (!active && !submitted &&
            connectionStalled(window.Date.now() - lastReplyMs)) {
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
      if (!submitted) submit.click();
    }

    function activate(status) {
      if (active) return;
      active = true;
      showCapacity(status);
      gate.hidden = true;
      content.hidden = false;
      send('operative');
      const remaining = Math.max(0, status.deadline_ts - status.server_now_ts);
      deadlineTimer = window.setTimeout(autoAdvance, remaining * 1000 + 150);
      if (pollTimer) window.clearTimeout(pollTimer);
    }

    submit.addEventListener('click', function () {
      submitted = true;
      if (deadlineTimer) window.clearTimeout(deadlineTimer);
    });

    window.liveRecv = function (status) {
      if (!status || typeof status !== 'object') return;
      lastReplyMs = window.Date.now();
      acknowledged = acknowledged || Boolean(status.acknowledged);
      if (status.technical_anomaly) {
        message.textContent = '本轮出现技术异常，请联系实验管理员。';
        if (pollTimer) window.clearTimeout(pollTimer);
        return;
      }
      if (status.deadline_ts && status.server_now_ts >= status.deadline_ts) {
        message.textContent = '本轮时间已结束，正在进入结果页面。';
        if (pollTimer) window.clearTimeout(pollTimer);
        autoAdvance();
        return;
      }
      if (canActivate(status, condition)) {
        activate(status);
        return;
      }
      if (status.release_ts) {
        message.textContent = '本房间已安排统一放行，即将开始 60 秒决策。';
        schedulePoll(Math.max(80,
          (status.release_ts - status.server_now_ts) * 1000 + 50));
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
