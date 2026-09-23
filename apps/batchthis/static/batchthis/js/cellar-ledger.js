// Collapsible sidebar rail for the Cellar Ledger redesign. Remembered per-browser.
(function () {
  var wrapper = document.getElementById('wrapper');
  if (!wrapper) return;

  var toggles = Array.prototype.slice.call(document.querySelectorAll('.js-rail-toggle'));

  function setCollapsed(state) {
    wrapper.setAttribute('data-collapsed', state ? 'true' : 'false');
    toggles.forEach(function (b) {
      var label = state ? 'Expand sidebar' : 'Collapse sidebar';
      b.setAttribute('aria-label', label);
      b.title = label;
    });
    try { localStorage.setItem('cellarledger-rail-collapsed', state ? '1' : '0'); } catch (err) {}
  }

  var stored = null;
  try { stored = localStorage.getItem('cellarledger-rail-collapsed'); } catch (err) {}
  setCollapsed(stored === '1');

  toggles.forEach(function (b) {
    b.addEventListener('click', function () {
      setCollapsed(wrapper.getAttribute('data-collapsed') !== 'true');
    });
  });
})();

// Wires up an "Add row" button for a Django formset using its empty_form as the
// clone template (rendered inside a hidden <template>, with __prefix__ placeholders -
// see includes/_fermentable_form_row.html and editFermentables.html for the pattern).
// Shared by editFermentables.html, editAdjuncts.html and editYeasts.html so the
// clone/mount logic exists in one place.
window.CellarLedger = window.CellarLedger || {};

CellarLedger.initFormsetAdd = function (opts) {
  var addButton = document.getElementById(opts.addButtonId);
  var rowSet = document.getElementById(opts.rowSetId);
  var template = document.getElementById(opts.templateId);
  var totalForms = document.getElementById(opts.totalFormsId);
  if (!addButton || !rowSet || !template || !totalForms) return;

  addButton.addEventListener('click', function () {
    var newIndex = parseInt(totalForms.value, 10);
    var html = template.innerHTML.split('__prefix__').join(newIndex);
    var wrapper = document.createElement('div');
    wrapper.innerHTML = html.trim();
    var newRow = wrapper.firstElementChild;
    rowSet.appendChild(newRow);
    totalForms.value = newIndex + 1;

    var modelSelectMount = newRow.querySelector('[data-react-model-select]');
    if (modelSelectMount && window.BatchThis && window.BatchThis.mountModelSelects) {
      window.BatchThis.mountModelSelects(newRow);
    }
    newRow.scrollIntoView({ block: 'nearest' });
  });
};

// Hand-drawn SVG line chart for instrument readings (specific gravity, pH, SO2, ...).
// Used by batch.html. cfg: { dates:[...], values:[...], decimals, unit, color,
// threshold, thresholdLabel } - threshold/thresholdLabel are optional; any point
// below `threshold` is drawn as a fault marker (see docs/flagged-notifications.md).
//
// Instead of (or as well as) a flat `threshold`, cfg can carry `bandMin`/`bandMax`
// arrays parallel to `values` - the min/max a StagedFaultRule allows at each
// reading's own timestamp (see faults.py: StagedFaultRule.bounds_at and
// views/main.py: _build_series). Drawn as a shaded band with dashed edges; any
// point outside its own bandMin/bandMax is a fault marker, same as `threshold`.

CellarLedger.renderChart = function (svg, cfg) {
  var values = cfg.values, labels = cfg.dates;
  if (!values || values.length === 0) return;
  var bandMin = cfg.bandMin, bandMax = cfg.bandMax;
  var hasBand = Array.isArray(bandMin) && Array.isArray(bandMax) && bandMin.length === values.length;

  var W = 640, H = 176, padL = 44, padR = 12, padT = 12, padB = 22;
  var innerW = W - padL - padR, innerH = H - padT - padB;

  var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
  var domMin = cfg.threshold != null ? Math.min(min, cfg.threshold) : min;
  var domMax = cfg.threshold != null ? Math.max(max, cfg.threshold) : max;
  if (hasBand) {
    domMin = Math.min(domMin, Math.min.apply(null, bandMin));
    domMax = Math.max(domMax, Math.max.apply(null, bandMax));
  }
  var span = (domMax - domMin) || 1;
  var yPad = span * 0.22;
  var yMin = domMin - yPad, yMax = domMax + yPad;
  var single = values.length === 1;

  function x(i) { return single ? padL + innerW / 2 : padL + (innerW * i) / (values.length - 1); }
  function y(v) { return padT + innerH - ((v - yMin) / (yMax - yMin)) * innerH; }

  var ns = 'http://www.w3.org/2000/svg';
  function el(tag, attrs) {
    var e = document.createElementNS(ns, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.innerHTML = '';

  var ticks = 4;
  for (var t = 0; t <= ticks; t++) {
    var v = yMin + (t / ticks) * (yMax - yMin);
    var yy = y(v);
    svg.appendChild(el('line', { x1: padL, x2: W - padR, y1: yy, y2: yy, class: 'cl-grid-line' }));
    var gl = el('text', { x: padL - 7, y: yy + 3, class: 'cl-grid-label', 'text-anchor': 'end' });
    gl.textContent = v.toFixed(cfg.decimals);
    svg.appendChild(gl);
  }

  if (hasBand) {
    var bandAreaD = 'M ' + x(0) + ' ' + y(bandMax[0]);
    for (var bi = 1; bi < bandMax.length; bi++) bandAreaD += ' L ' + x(bi) + ' ' + y(bandMax[bi]);
    for (var bj = bandMin.length - 1; bj >= 0; bj--) bandAreaD += ' L ' + x(bj) + ' ' + y(bandMin[bj]);
    bandAreaD += ' Z';
    svg.appendChild(el('path', { d: bandAreaD, class: 'cl-band-fill' }));

    var maxLineD = 'M ' + x(0) + ' ' + y(bandMax[0]);
    var minLineD = 'M ' + x(0) + ' ' + y(bandMin[0]);
    for (var bk = 1; bk < values.length; bk++) {
      maxLineD += ' L ' + x(bk) + ' ' + y(bandMax[bk]);
      minLineD += ' L ' + x(bk) + ' ' + y(bandMin[bk]);
    }
    svg.appendChild(el('path', { d: maxLineD, class: 'cl-band-line' }));
    svg.appendChild(el('path', { d: minLineD, class: 'cl-band-line' }));

    var blab = el('text', { x: W - padR, y: y(bandMax[bandMax.length - 1]) - 5, class: 'cl-threshold-label', 'text-anchor': 'end' });
    blab.textContent = cfg.bandLabel || 'expected range';
    svg.appendChild(blab);
  } else if (cfg.threshold != null) {
    var ty = y(cfg.threshold);
    svg.appendChild(el('line', { x1: padL, x2: W - padR, y1: ty, y2: ty, class: 'cl-threshold-line' }));
    var tlab = el('text', { x: W - padR, y: ty - 5, class: 'cl-threshold-label', 'text-anchor': 'end' });
    tlab.textContent = cfg.thresholdLabel || ('min. ' + cfg.threshold);
    svg.appendChild(tlab);
  }

  labels.forEach(function (lab, i) {
    var anchor = single ? 'middle' : (i === 0 ? 'start' : (i === labels.length - 1 ? 'end' : 'middle'));
    var lx = el('text', { x: x(i), y: H - 5, class: 'cl-x-label', 'text-anchor': anchor });
    lx.textContent = lab;
    svg.appendChild(lx);
  });

  if (!single) {
    var d = 'M ' + x(0) + ' ' + y(values[0]);
    for (var i = 1; i < values.length; i++) d += ' L ' + x(i) + ' ' + y(values[i]);
    var areaD = d + ' L ' + x(values.length - 1) + ' ' + (padT + innerH) + ' L ' + x(0) + ' ' + (padT + innerH) + ' Z';
    svg.appendChild(el('path', { d: areaD, class: 'cl-area-fill', style: 'fill:' + cfg.color }));
    svg.appendChild(el('path', { d: d, class: 'cl-line-path', style: 'stroke:' + cfg.color }));
  }

  values.forEach(function (v, i) {
    var isLast = i === values.length - 1;
    var isFault = hasBand ? (v < bandMin[i] || v > bandMax[i]) : (cfg.threshold != null && v < cfg.threshold);
    var r = (isLast || isFault) ? 4.5 : 3;
    var cls = isFault ? 'cl-pt-fault' : (isLast ? 'cl-pt-end' : 'cl-pt');
    var style = isFault ? '' : (isLast ? 'fill:' + cfg.color : 'stroke:' + cfg.color);
    svg.appendChild(el('circle', { cx: x(i), cy: y(v), r: r, class: cls, style: style }));
  });

  if (single) return;

  var crosshair = el('line', { x1: x(0), x2: x(0), y1: padT, y2: padT + innerH, class: 'cl-crosshair', style: 'stroke:' + cfg.color + ';opacity:0' });
  svg.appendChild(crosshair);

  var hit = el('rect', { x: padL, y: padT, width: innerW, height: innerH, class: 'cl-hit-rect' });
  svg.appendChild(hit);

  var wrap = svg.closest('.cl-chart-wrap');
  var tooltip = wrap ? wrap.querySelector('.cl-chart-tooltip') : null;
  var hitW = innerW / (values.length - 1);

  hit.addEventListener('mousemove', function (e) {
    var rect = svg.getBoundingClientRect();
    var px = (e.clientX - rect.left) * (W / rect.width);
    var idx = Math.round((px - padL) / hitW);
    idx = Math.max(0, Math.min(values.length - 1, idx));
    crosshair.setAttribute('x1', x(idx));
    crosshair.setAttribute('x2', x(idx));
    crosshair.style.opacity = 1;
    if (tooltip) {
      tooltip.hidden = false;
      tooltip.style.left = ((x(idx) / W) * 100) + '%';
      tooltip.querySelector('.tt-date').textContent = labels[idx];
      tooltip.querySelector('.tt-val').textContent = values[idx].toFixed(cfg.decimals) + (cfg.unit || '');
    }
  });
  hit.addEventListener('mouseleave', function () {
    crosshair.style.opacity = 0;
    if (tooltip) tooltip.hidden = true;
  });
};

// Refractometer correction modal (base.html) — reusable from any page. A trigger opens it with
// data-target-field="<selector>"; "Use this value" writes the corrected gravity into that field
// and fires a native "change" event so any existing listener on it (e.g. addBatch.html's batch
// name builder) still reacts.
(function ($) {
  if (!$) return;
  var $modal = $('#refractometerModal');
  if (!$modal.length) return;

  var targetFieldSelector = null;
  var lastResult = null;

  function reset() {
    lastResult = null;
    $('#rcm-result').attr('hidden', true);
    $('#rcm-error').attr('hidden', true);
    $('#rcm-apply').prop('disabled', true);
  }

  $modal.on('show.bs.modal', function (event) {
    var $trigger = $(event.relatedTarget);
    targetFieldSelector = $trigger.data('target-field') || null;
    reset();
  });

  $('#rcm-calculate').on('click', function () {
    var params = {};
    var startUnit = $('#rcm-startUnit').val();
    var currentUnit = $('#rcm-currentUnit').val();
    var startData = parseFloat($('#rcm-startData').val());
    var currentData = parseFloat($('#rcm-currentData').val());

    if (startUnit === 'sg') { params.startSG = startData; } else { params.startBrix = startData; }
    if (currentUnit === 'sg') { params.currentSG = currentData; } else { params.currentBrix = currentData; }

    BatchUtilsRPC.refractometerCorrection(params).done(function (data) {
      lastResult = data;
      $('#rcm-sg').text(parseFloat(data.sg).toFixed(3));
      $('#rcm-abv').html(parseFloat(data.abv).toFixed(1) + ' <small>%</small>');
      $('#rcm-result').removeAttr('hidden');
      $('#rcm-error').attr('hidden', true);
      $('#rcm-apply').prop('disabled', false);
    }).fail(function () {
      $('#rcm-result').attr('hidden', true);
      $('#rcm-apply').prop('disabled', true);
      $('#rcm-error').removeAttr('hidden');
    });
  });

  $('#rcm-apply').on('click', function () {
    if (!lastResult || !targetFieldSelector) return;
    var $target = $(targetFieldSelector);
    $target.val(parseFloat(lastResult.sg).toFixed(3));
    $target[0].dispatchEvent(new Event('change', { bubbles: true }));
    $modal.modal('hide');
  });
})(window.jQuery);

// Mouse-following tooltips (e.g. yeast/fermentable/adjunct names showing their notes) —
// used on recipe.html and the includes/recipe_additions.html partial (addRecipe2.html).
(function ($) {
  if (!$) return;
  $(document).ready(function () {
    var $mouseTooltips = $('.js-mouse-tooltip').filter(function () {
      return $.trim($(this).attr('title')) !== '';
    });
    if (!$mouseTooltips.length) return;

    $mouseTooltips.tooltip({
      trigger: 'manual',
      placement: 'top',
      animation: false,
      template: '<div class="tooltip cl-mouse-tooltip" role="tooltip"><div class="arrow"></div><div class="tooltip-inner"></div></div>'
    });

    $mouseTooltips.on('mouseenter', function () {
      $(this).tooltip('show');
    }).on('mousemove', function (e) {
      var tipId = $(this).attr('aria-describedby');
      if (tipId) {
        // Popper (used internally by the tooltip) positions the tooltip with its own
        // "transform: translate3d(...)" — it must be cleared, or it stacks on top of
        // the top/left below and throws the tooltip off the intended position.
        $('#' + tipId).css({
          position: 'fixed',
          top: (e.clientY + 16) + 'px',
          left: (e.clientX + 16) + 'px',
          margin: 0,
          transform: 'none'
        });
      }
    }).on('mouseleave', function () {
      $(this).tooltip('hide');
    });
  });
})(window.jQuery);

// Batch time bar (batch.html): on narrow screens the bar scrolls sideways, and the
// current stay is usually off to the right - bring it into view on load.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.cl-timebar-scroll').forEach(function (scroller) {
    var current = scroller.querySelector('.cl-timebar-seg--current');
    if (!current || scroller.scrollWidth <= scroller.clientWidth) return;
    scroller.scrollLeft = Math.max(0, current.offsetLeft - scroller.offsetLeft - 16);
  });
});
