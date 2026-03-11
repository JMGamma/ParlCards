(function () {
  'use strict';

  // ── Histogram helpers ───────────────────────────────────────────────────────

  function barColor(pct) {
    if (pct >= 75) return '#22c55e';
    if (pct >= 50) return '#f59e0b';
    if (pct >= 25) return '#f97316';
    return '#ef4444';
  }

  function ordinal(n) {
    n = Math.round(n);
    var s = ['th', 'st', 'nd', 'rd'];
    var v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  function fmtVal(val) {
    if (val === Math.floor(val)) return String(Math.floor(val));
    return val.toFixed(1);
  }

  function buildHistHtml(pct, dist, unit) {
    if (pct === null || pct === undefined) {
      return '<div class="relative h-2.5 bg-gray-100 rounded-full overflow-hidden">' +
        '<div class="h-full rounded-full bg-gray-200" style="width:50%"></div></div>' +
        '<div class="text-right text-xs text-gray-400 mt-1">Rankings loading\u2026</div>';
    }
    if (!dist) {
      var c2 = barColor(pct);
      return '<div class="relative h-2.5 bg-gray-100 rounded-full overflow-hidden">' +
        '<div class="h-full rounded-full" style="width:' + pct + '%;background-color:' + c2 + '"></div></div>' +
        '<div class="text-right text-xs text-gray-400 mt-1">' + ordinal(pct) + ' percentile</div>';
    }
    var buckets = dist.buckets;
    var mpBucket = dist.mp_bucket;
    var maxCount = Math.max.apply(null, buckets);
    var n = buckets.length;
    var bw = 200 / n;
    var color = barColor(pct);
    var rects = '';
    for (var i = 0; i < buckets.length; i++) {
      var barH = Math.max(2, Math.round((buckets[i] / maxCount) * 38));
      var x = i * bw;
      var y = 40 - barH;
      var fill = i === mpBucket ? color : '#E5E7EB';
      rects += '<rect x="' + (x + 0.75).toFixed(2) + '" y="' + y +
        '" width="' + (bw - 1.5).toFixed(2) + '" height="' + barH +
        '" fill="' + fill + '" rx="1.5"/>';
    }
    var loLabel = fmtVal(dist.lo) + unit;
    var hiLabel = fmtVal(dist.hi) + unit;
    return '<svg viewBox="0 0 200 40" class="w-full" preserveAspectRatio="none" style="height:40px;display:block;">' +
      rects + '</svg>' +
      '<div class="flex justify-between text-xs text-gray-400 mt-0.5">' +
      '<span>' + loLabel + '</span>' +
      '<span>' + ordinal(pct) + ' percentile</span>' +
      '<span>' + hiLabel + '</span></div>';
  }

  // ── Group toggle ────────────────────────────────────────────────────────────

  var BASE_BTN = 'text-xs px-3 py-1.5 rounded-full font-semibold border transition-colors';
  var ACTIVE   = ' bg-gray-900 text-white border-gray-900';
  var INACTIVE = ' bg-white text-gray-500 border-gray-200 hover:border-gray-400';

  function initGroupToggle(byGroup) {
    document.querySelectorAll('[data-group]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var groupKey = btn.dataset.group;
        var gd = byGroup[groupKey];
        if (!gd) return;

        // Update button active states
        document.querySelectorAll('[data-group]').forEach(function (b) {
          b.className = BASE_BTN + (b.dataset.group === groupKey ? ACTIVE : INACTIVE);
        });

        // Update each metric's histogram (including hidden ones — content stays fresh)
        document.querySelectorAll('[data-metric]').forEach(function (metricEl) {
          if (metricEl.dataset.na === 'true') return;
          var metric = metricEl.dataset.metric;
          var unit = metricEl.dataset.unit || '';
          var pct = gd.percentiles ? gd.percentiles[metric] : undefined;
          var dist = gd.distributions ? gd.distributions[metric] : undefined;
          var wrapper = metricEl.querySelector('.stat-hist-wrapper');
          if (wrapper) wrapper.innerHTML = buildHistHtml(pct, dist, unit);
        });

        // Update footer text
        var footer = document.getElementById('group-footer');
        if (footer) {
          var partyLabel = footer.dataset.party || 'same party';
          var session = footer.dataset.session || '';
          var labels = {
            all: 'all current MPs',
            party: partyLabel,
            government: 'government MPs',
            opposition: 'opposition MPs',
          };
          footer.textContent = 'Ranked against ' + (labels[groupKey] || groupKey) + ' \u00b7 Session ' + session;
        }
      });
    });
  }

  // ── Metric toggle ───────────────────────────────────────────────────────────

  var STORAGE_KEY = 'parl_hidden_metrics';

  // Button classes: ON = metric is visible, OFF = metric is hidden
  var TOGGLE_ON  = 'text-xs px-3 py-1.5 rounded-full font-semibold border transition-colors bg-gray-800 text-white border-gray-800';
  var TOGGLE_OFF = 'text-xs px-3 py-1.5 rounded-full font-semibold border transition-colors bg-white text-gray-400 border-gray-200 hover:border-gray-400';

  function getHiddenMetrics() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) { return []; }
  }

  function saveHiddenMetrics(arr) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(arr)); } catch (e) {}
  }

  function applyMetricVisibility(hidden) {
    document.querySelectorAll('[data-toggle-metric]').forEach(function (btn) {
      var metric = btn.dataset.toggleMetric;
      var isHidden = hidden.indexOf(metric) >= 0;
      btn.className = isHidden ? TOGGLE_OFF : TOGGLE_ON;
      var metricEl = document.querySelector('[data-metric="' + metric + '"]');
      if (metricEl) metricEl.style.display = isHidden ? 'none' : '';
    });
  }

  function initMetricToggles() {
    // Apply saved state immediately on load
    applyMetricVisibility(getHiddenMetrics());

    document.querySelectorAll('[data-toggle-metric]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var metric = btn.dataset.toggleMetric;
        var hidden = getHiddenMetrics();
        var idx = hidden.indexOf(metric);
        if (idx >= 0) {
          hidden.splice(idx, 1);   // was hidden → now visible
        } else {
          hidden.push(metric);      // was visible → now hidden
        }
        saveHiddenMetrics(hidden);
        applyMetricVisibility(hidden);
      });
    });
  }

  // ── Init ────────────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    var dataEl = document.getElementById('card-data');
    if (!dataEl) return;
    var byGroup;
    try { byGroup = JSON.parse(dataEl.textContent); } catch (e) { return; }

    initGroupToggle(byGroup);
    initMetricToggles();
  });
}());
