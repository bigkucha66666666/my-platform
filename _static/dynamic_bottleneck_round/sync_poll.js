/* Bounded, staggered reloads; ready transitions retain the experiment's delay. */
(function (window) {
    'use strict';

    function start(panelId, buttonId) {
        const panel = window.document.getElementById(panelId);
        const key = 'dynamic-bottleneck-sync:' + window.location.pathname + ':' + panelId;
        const ready = panel.dataset.ready === '1';
        let attempt = 0;
        try {
            if (ready) {
                window.sessionStorage.removeItem(key);
            } else {
                attempt = Math.min(10, Math.max(0, Number(window.sessionStorage.getItem(key)) || 0));
                window.sessionStorage.setItem(key, String(attempt + 1));
            }
        } catch (_error) {
            // Private/locked browser storage must never prevent progression.
        }
        if (ready) {
            window.setTimeout(function () {
                window.document.getElementById(buttonId).click();
            }, Number(panel.dataset.delay) || 200);
            return;
        }
        const base = Math.max(1500, Number(panel.dataset.poll) || 1500);
        const delay = Math.min(3500, base * Math.pow(1.35, attempt))
            + Math.floor(window.Math.random() * 500);
        window.setTimeout(function () {
            window.location.reload();
        }, delay);
    }

    window.DynamicBottleneckSync = {start: start};
})(window);
