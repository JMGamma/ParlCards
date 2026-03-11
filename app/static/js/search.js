(function () {
  'use strict';

  var politicians = null;

  var PARTY_COLORS = {
    liberal:      '#D71920',
    conservative: '#1A4782',
    ndp:          '#F37021',
    bloc:         '#00B0F0',
    green:        '#3D9B35',
    independent:  '#6B7280',
  };

  function partyColor(slug) {
    return PARTY_COLORS[slug] || PARTY_COLORS.independent;
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function buildResultsHtml(results) {
    if (!results.length) return '';
    var items = results.slice(0, 8).map(function (p) {
      var color = partyColor(p.party_slug || 'independent');
      var name0 = escHtml(p.name[0] || '?');
      var photo = p.photo_url
        ? '<img src="' + escHtml(p.photo_url) + '" alt="' + escHtml(p.name) + '"' +
          ' class="w-9 h-9 rounded-full object-cover flex-shrink-0 border-2"' +
          ' style="border-color:' + color + '"' +
          ' onerror="this.style.display=\'none\'">'
        : '<div class="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center text-sm text-white font-bold"' +
          ' style="background-color:' + color + '">' + name0 + '</div>';
      var partyLabel = escHtml(p.party || 'Ind.');
      var riding = p.riding
        ? '<span class="text-xs text-gray-400 truncate">' + escHtml(p.riding) +
          (p.province ? ', ' + escHtml(p.province) : '') + '</span>'
        : (p.province ? '<span class="text-xs text-gray-400">' + escHtml(p.province) + '</span>' : '');
      return '<li>' +
        '<a href="/politicians/' + escHtml(p.slug) + '/"' +
        ' class="flex items-center gap-3 px-4 py-3 hover:bg-gray-50 transition-colors">' +
        photo +
        '<div class="min-w-0 flex-1">' +
        '<span class="font-medium text-gray-900 text-sm">' + escHtml(p.name) + '</span>' +
        '<div class="flex items-center gap-2 mt-0.5">' +
        '<span class="text-xs font-medium px-1.5 py-0.5 rounded text-white"' +
        ' style="background-color:' + color + '">' + partyLabel + '</span>' +
        riding +
        '</div></div>' +
        '<svg class="w-4 h-4 text-gray-300 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>' +
        '</svg></a></li>';
    });
    return '<ul class="divide-y divide-gray-50">' + items.join('') + '</ul>';
  }

  function filterPoliticians(query) {
    var q = query.toLowerCase().trim();
    if (!q) return [];
    return politicians.filter(function (p) {
      return (
        p.name.toLowerCase().includes(q) ||
        (p.riding && p.riding.toLowerCase().includes(q)) ||
        (p.province && p.province.toLowerCase().includes(q)) ||
        (p.party && p.party.toLowerCase().includes(q))
      );
    });
  }

  function loadPoliticians() {
    if (politicians !== null) return Promise.resolve();
    return fetch('/static/politicians.json')
      .then(function (r) { return r.json(); })
      .then(function (data) { politicians = data; })
      .catch(function () { politicians = []; });
  }

  function wireInput(input) {
    var targetId = input.dataset.target;
    if (!targetId) return;
    var target = document.getElementById(targetId);
    if (!target) return;

    var timer = null;

    input.addEventListener('input', function () {
      clearTimeout(timer);
      var q = input.value;
      if (!q.trim()) { target.innerHTML = ''; return; }
      timer = setTimeout(function () {
        loadPoliticians().then(function () {
          target.innerHTML = buildResultsHtml(filterPoliticians(q));
        });
      }, 250);
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { input.value = ''; target.innerHTML = ''; }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.search-input').forEach(wireInput);

    document.addEventListener('click', function (e) {
      document.querySelectorAll('.search-input').forEach(function (input) {
        var targetId = input.dataset.target;
        if (!targetId) return;
        var target = document.getElementById(targetId);
        if (!target) return;
        if (!input.contains(e.target) && !target.contains(e.target)) {
          target.innerHTML = '';
        }
      });
    });
  });
}());
