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

// Up/down buttons ([data-move="up|down"]) on each row of a formset, for rows whose
// order matters (plan steps - see includes/_plan_step_row.html). Rows are only
// moved on the page; on submit every row's field names/ids are renumbered to its
// position, so the server reads the rows in page order (form index = order).
CellarLedger.initFormsetReorder = function (opts) {
  var rowSet = document.getElementById(opts.rowSetId);
  var form = document.getElementById(opts.formId);
  if (!rowSet || !form) return;
  var pattern = new RegExp('^(id_)?' + opts.prefix + '-\\d+-');

  rowSet.addEventListener('click', function (e) {
    var button = e.target.closest('[data-move]');
    if (!button || !rowSet.contains(button)) return;
    var row = button.closest('[data-formset-row]');
    if (button.getAttribute('data-move') === 'up' && row.previousElementSibling) {
      rowSet.insertBefore(row, row.previousElementSibling);
    } else if (button.getAttribute('data-move') === 'down' && row.nextElementSibling) {
      rowSet.insertBefore(row.nextElementSibling, row);
    }
    // Keep focus on the moved row; if this button is now hidden (row reached an end), use its twin.
    var target = window.getComputedStyle(button).visibility === 'hidden'
      ? row.querySelector('[data-move]:not([data-move="' + button.getAttribute('data-move') + '"])') : button;
    if (target) target.focus();
  });

  form.addEventListener('submit', function () {
    Array.prototype.forEach.call(rowSet.querySelectorAll('[data-formset-row]'), function (row, index) {
      Array.prototype.forEach.call(row.querySelectorAll('[name], [id], label[for]'), function (el) {
        ['name', 'id', 'for'].forEach(function (attr) {
          var value = el.getAttribute(attr);
          if (value && pattern.test(value)) {
            el.setAttribute(attr, value.replace(pattern, function (m, id) { return (id || '') + opts.prefix + '-' + index + '-'; }));
          }
        });
      });
    });
  });
};

// Plan step rows: when a stage is chosen, only the vessel types it allows stay
// selectable (Pitch -> Fermenter, Complete Batch -> None / Current: pre-set and
// locked), and a stage with no duration (Complete Batch) clears and disables it.
// Rules come from the row set's data-stage-rules (forms.BasePlanStepFormSet.stage_rules);
// the server checks the same rules on save.
CellarLedger.initPlanSteps = function (opts) {
  var rowSet = document.getElementById(opts.rowSetId);
  if (!rowSet) return;
  var rules = {};
  try { rules = JSON.parse(rowSet.getAttribute('data-stage-rules') || '{}'); } catch (err) {
    console.error('initPlanSteps: bad data-stage-rules', err);
  }

  function apply(row) {
    var stage = row.querySelector('select[name$="-stage"]');
    var vesselType = row.querySelector('select[name$="-vessel_type"]');
    var duration = row.querySelector('input[name$="-planned_duration"]');
    if (!stage || !vesselType) return;
    var rule = rules[stage.value];
    var allowed = rule ? rule.vessel_types : null;
    Array.prototype.forEach.call(vesselType.options, function (option) {
      option.disabled = !!allowed && (option.value === '' ? allowed.length === 1 : allowed.indexOf(option.value) === -1);
    });
    if (allowed && allowed.length === 1) {
      vesselType.value = allowed[0];
    } else if (allowed && vesselType.value && allowed.indexOf(vesselType.value) === -1) {
      vesselType.value = '';
    }
    if (duration) {
      var noDuration = !!rule && rule.duration === false;
      if (noDuration) duration.value = '';
      duration.disabled = noDuration;
      duration.placeholder = noDuration ? 'No duration' : 'e.g. 14 days';
    }
  }

  Array.prototype.forEach.call(rowSet.querySelectorAll('[data-formset-row]'), apply);
  rowSet.addEventListener('change', function (e) {
    if (e.target.matches('select[name$="-stage"]')) apply(e.target.closest('[data-formset-row]'));
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

  // Triggers with data-cl-refractometer (e.g. includes/_test_form.html) open it here;
  // inside #formModal the form modal hands off to it instead (see below).
  $(document).on('click', '[data-cl-refractometer]', function () {
    if (this.closest('#formModal')) return;
    $modal.modal('show', this);
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
        var $tip = $('#' + tipId);
        var offset = 16, edge = 8;
        var width = $tip.outerWidth(), height = $tip.outerHeight();
        var left = e.clientX + offset, top = e.clientY + offset;
        // Keep it on screen: flip to the cursor's left/above when it would run off the
        // right/bottom edge, and never past the top/left edge.
        if (left + width > window.innerWidth - edge) left = e.clientX - offset - width;
        if (top + height > window.innerHeight - edge) top = e.clientY - offset - height;
        $tip.css({
          position: 'fixed',
          top: Math.max(edge, top) + 'px',
          left: Math.max(edge, left) + 'px',
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

// Actions menus ([data-cl-menu], e.g. the batch page's ellipsis menu) - WAI-ARIA menu
// button: click/Enter/Space or ArrowDown opens and focuses the first item (ArrowUp the
// last); arrows/Home/End move between items; Escape closes and returns focus to the
// button; Tab or a click outside closes it.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-cl-menu]').forEach(function (root) {
    var toggle = root.querySelector('.cl-menu-toggle');
    var list = root.querySelector('[role="menu"]');
    if (!toggle || !list) {
      console.error('cl-menu: missing .cl-menu-toggle or [role="menu"]', root);
      return;
    }
    function items() { return Array.prototype.slice.call(list.querySelectorAll('[role="menuitem"]')); }
    function open(index) {
      list.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      var all = items();
      if (all.length) all[index < 0 ? all.length - 1 : index].focus();
    }
    function close(returnFocus) {
      if (list.hidden) return;
      list.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      if (returnFocus) toggle.focus();
    }

    toggle.addEventListener('click', function () { list.hidden ? open(0) : close(false); });
    toggle.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); open(0); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); open(-1); }
    });
    list.addEventListener('keydown', function (e) {
      var all = items();
      var i = all.indexOf(document.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); close(true); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); all[(i + 1) % all.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); all[(i - 1 + all.length) % all.length].focus(); }
      else if (e.key === 'Home') { e.preventDefault(); all[0].focus(); }
      else if (e.key === 'End') { e.preventDefault(); all[all.length - 1].focus(); }
      else if (e.key === 'Tab') { close(false); }
    });
    document.addEventListener('click', function (e) {
      if (!root.contains(e.target)) close(false);
    });
  });
});

// Shared form modal (#formModal in base.html). A link with data-cl-form-modal opens
// its href's form here instead of navigating: the view answers a modal request
// (X-Requested-With) with just the form; submitting posts it in the background -
// errors re-render inside the modal, and {"saved": true} reloads the page so the
// newest data shows. Without JavaScript the link still opens the full page.
(function ($) {
  if (!$) return;
  $(document).ready(function () {
    var $modal = $('#formModal');
    if (!$modal.length) return;
    var body = document.getElementById('formModalBody');
    var title = document.getElementById('formModalLabel');
    var opener = null;
    var handoff = false;      // true while #refractometerModal is open on top of this form
    var returnFocus = null;   // field to focus when the form modal comes back
    var headers = { 'X-Requested-With': 'XMLHttpRequest' };

    function contentLoaded() {
      // Scripts in inserted HTML don't run; per-form behavior listens for this instead.
      document.dispatchEvent(new CustomEvent('cl:content-loaded', { detail: { root: body } }));
    }

    function focusFirst(selector) {
      // react-select renders its input a tick after mounting.
      setTimeout(function () {
        var el = body.querySelector(selector) ||
          body.querySelector('.cl-field input:not([type=hidden]), .cl-field select, .cl-field textarea');
        if (el) el.focus();
      }, 100);
    }

    function showMessage(html) {
      body.innerHTML = '<div class="cl-flag"><span class="cl-flag-mark">!</span><span>' + html + '</span></div>';
    }

    function bindForm(url) {
      if (window.BatchThis && window.BatchThis.mountModelSelects) window.BatchThis.mountModelSelects(body);
      var form = body.querySelector('form');
      if (!form) return;
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var submit = form.querySelector('[type=submit]');
        if (submit) submit.disabled = true;
        fetch(form.getAttribute('action') || url, {
          method: 'POST', body: new FormData(form), headers: headers, credentials: 'same-origin'
        }).then(function (response) {
          if (!response.ok) throw new Error('HTTP ' + response.status);
          var type = response.headers.get('content-type') || '';
          return type.indexOf('application/json') !== -1 ? response.json() : response.text();
        }).then(function (data) {
          if (data && data.saved) { window.location.reload(); return; }
          body.innerHTML = data;
          bindForm(url);
          contentLoaded();
          focusFirst('[aria-invalid="true"]');
        }).catch(function (err) {
          console.error('form modal: saving ' + url + ' failed', err);
          if (submit) submit.disabled = false;
          showMessage('Couldn\'t save. Check your connection and try again, or <a href="' + url + '">open it as a page</a>.');
        });
      });
    }

    $(document).on('click', '[data-cl-form-modal]', function (e) {
      e.preventDefault();
      var url = this.getAttribute('href');
      // Opened from an actions menu: close it, and return focus to its button afterwards.
      var menu = this.closest('[data-cl-menu]');
      opener = this;
      if (menu) {
        var toggle = menu.querySelector('.cl-menu-toggle');
        menu.querySelector('[role="menu"]').hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
        opener = toggle;
      }
      title.textContent = this.getAttribute('data-modal-title') || this.textContent.trim();
      body.innerHTML = '<p class="cl-empty">Loading&hellip;</p>';
      $modal.modal('show');
      fetch(url, { headers: headers, credentials: 'same-origin' }).then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.text();
      }).then(function (html) {
        body.innerHTML = html;
        bindForm(url);
        contentLoaded();
        focusFirst();
      }).catch(function (err) {
        console.error('form modal: loading ' + url + ' failed', err);
        showMessage('Couldn\'t load the form. <a href="' + url + '">Open it as a page</a> instead.');
      });
    });

    // Bootstrap focuses the dialog itself once its opening animation ends - move
    // focus on to the first field then (loading the form also focuses it).
    $modal.on('shown.bs.modal', function () {
      if (returnFocus) {
        var field = returnFocus;
        returnFocus = null;
        setTimeout(function () { field.focus(); }, 50);
        return;
      }
      focusFirst();
    });

    // "Use refractometer" inside the form: Bootstrap 4 can't stack modals (the first
    // keeps pulling focus back), so hand off - hide this modal WITHOUT clearing the
    // half-filled form, open the refractometer, and bring the form back (focused on
    // the field it filled) when the refractometer closes.
    $(body).on('click', '[data-cl-refractometer]', function () {
      var button = this;
      var $refractometer = $('#refractometerModal');
      handoff = true;
      $modal.one('hidden.bs.modal', function () { $refractometer.modal('show', button); });
      $refractometer.one('hidden.bs.modal', function () {
        handoff = false;
        returnFocus = document.querySelector(button.getAttribute('data-target-field'));
        $modal.modal('show');
      });
      $modal.modal('hide');
    });

    // Escape with a react-select dropdown open should close just the dropdown, not
    // the whole modal (and the half-filled form). React closes the menu before the
    // key bubbles back up, so note whether one was open on the way down (capture)
    // and stop the key from reaching Bootstrap on the way up.
    var dropdownWasOpen = false;
    body.addEventListener('keydown', function (e) {
      dropdownWasOpen = e.key === 'Escape' && !!body.querySelector('.model-select__menu');
    }, true);
    body.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && dropdownWasOpen) e.stopPropagation();
      dropdownWasOpen = false;
    });

    $modal.on('hidden.bs.modal', function () {
      if (handoff) return;  // stepping aside for the refractometer - keep the form
      body.innerHTML = '';
      if (opener) opener.focus();
      opener = null;
    });
  });
})(window.jQuery);


// Test reading forms (includes/_test_form.html, on addTest.html and in #formModal):
// the Value placeholder shows the units the chosen test type takes, and "Use
// refractometer" only shows for Specific Gravity. Runs on page load and whenever
// the form modal loads new content (cl:content-loaded).
(function () {
  function initReadingForms(root) {
    root.querySelectorAll('form[data-reading-examples]:not([data-reading-init])').forEach(function (form) {
      form.setAttribute('data-reading-init', '');
      var typeSelect = form.querySelector('select[name="type"]');
      var valueInput = form.querySelector('input[name="value"]');
      var refractometer = form.querySelector('[data-cl-refractometer]');
      if (!typeSelect || !valueInput) {
        console.error('reading form: missing type select or value input', form);
        return;
      }
      var examples = {};
      try {
        examples = JSON.parse(form.getAttribute('data-reading-examples') || '{}');
      } catch (err) {
        console.error('reading form: bad data-reading-examples', err);
      }
      function update() {
        var option = typeSelect.options[typeSelect.selectedIndex];
        if (refractometer) refractometer.hidden = !option || option.text !== 'Specific Gravity';
        valueInput.placeholder = examples[typeSelect.value] || 'e.g. 1.050';
      }
      typeSelect.addEventListener('change', update);
      update();
    });
  }
  document.addEventListener('DOMContentLoaded', function () { initReadingForms(document); });
  document.addEventListener('cl:content-loaded', function (e) { initReadingForms(e.detail.root); });
})();
