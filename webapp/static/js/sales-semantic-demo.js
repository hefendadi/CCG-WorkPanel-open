/**
 * ACT Sales Semantic Explorer — Architecture Spike demo frontend.
 *
 * Design rule for this spike: the page contains NO per-view logic. It renders
 * whatever Metric / Dimension metadata the API returns, and every interaction —
 * including all four presets — issues the same POST /api/v1/lab/sales/query.
 * The existing dedicated dashboard endpoints are never called from here.
 *
 * All quantities shown are SYNTHETIC demo data. The Category preview at the
 * bottom is generated locally in the browser and is explicitly SIMULATED; it is
 * not part of the semantic engine.
 */
(() => {
  'use strict';

  const META_API = '/api/v1/lab/sales/semantic';
  const QUERY_API = '/api/v1/lab/sales/query';
  const OPTIONS_API = '/api/v1/lab/sales/filter-options';
  const MAX_GROUP_BY = 3;

  const state = {
    metadata: null,
    presets: [],
    metrics: [],
    dimensions: [],
    options: {},
    batchId: null,
    metric: 'actual_qty',
    dimensions: [],
    filters: {},
    sort: 'actual_qty:desc',
    lastResult: null,
    requestCount: 0,
  };

  // ---------------------------------------------------------------------
  // helpers
  // ---------------------------------------------------------------------
  const el = (id) => document.getElementById(id);

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const fmt = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return esc(value);
    return number.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 4 });
  };

  // Turns any lab API error body into something a reviewer can act on. Two detail
  // shapes reach this page:
  //   * the semantic engine's stable object — {detail: {code, message, details}}
  //   * FastAPI request validation — {detail: [{loc, msg, type, input}, ...]}
  // The second one used to collapse to a bare "HTTP 422", because this code only
  // looked for detail.message / detail.code and a list has neither.
  function describeHttpError(payload, status) {
    const fallback = `HTTP ${status}`;
    const detail = payload && payload.detail;
    if (detail === undefined || detail === null) {
      return payload && payload.message ? `${fallback} · ${payload.message}` : fallback;
    }
    if (typeof detail === 'string') return `${fallback} · ${detail}`;
    if (Array.isArray(detail)) {
      if (!detail.length) return fallback;
      const lines = [`${fallback} · 请求字段校验失败 (${detail.length})`];
      detail.forEach((item) => {
        if (!item || typeof item !== 'object') { lines.push(`  - ${String(item)}`); return; }
        const path = (Array.isArray(item.loc) ? item.loc : []).filter((part) => part !== 'body');
        lines.push(`  - field: ${path.join('.') || '(request body)'}`);
        lines.push(`    reason: ${item.msg || item.type || 'invalid value'}`);
        if (item.type) lines.push(`    rule: ${item.type}`);
      });
      return lines.join('\n');
    }
    if (typeof detail === 'object') {
      const lines = [
        detail.code ? `${detail.code}: ${detail.message || ''}`.trim()
          : (detail.message || fallback),
      ];
      const details = detail.details;
      if (details && typeof details === 'object') {
        Object.entries(details).forEach(([key, value]) => {
          lines.push(`  - field: ${key}`);
          lines.push(`    reason: ${Array.isArray(value) ? value.join(', ') : String(value)}`);
        });
      }
      return lines.join('\n');
    }
    return fallback;
  }

  // The lab API is gated by require_permission(..., current_user), which reads a
  // Bearer token from the Authorization header — not the portal cookie that only
  // protects the page route. common.js owns that token (localStorage 'sales_token')
  // and its authFetch also handles the 401 -> /login redirect, so every request
  // here goes through it.
  async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || { 'Content-Type': 'application/json' });
    const response = await authFetch(url, { ...options, headers });
    let payload = null;
    try { payload = await response.json(); } catch { /* non-JSON error body */ }
    if (!response.ok) {
      const error = new Error(describeHttpError(payload, response.status));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  // ---------------------------------------------------------------------
  // metadata + options
  // ---------------------------------------------------------------------
  async function loadMetadata() {
    const metadata = await apiFetch(META_API);
    state.metadata = metadata;
    state.metrics = metadata.metrics;
    // `state.dimensions` is the user's GROUP BY *selection* (dimension keys).
    // The available dimension metadata lives in state.metadata.dimensions, so the
    // two must never be conflated: storing the metadata objects here made the
    // group-by count equal the number of available dimensions and tripped the
    // 3-dimension guard on the very first automatic query.
    state.dimensions = [];
    state.presets = metadata.presets || [];
    state.metric = metadata.metrics[0] ? metadata.metrics[0].key : 'actual_qty';

    el('sem-subtitle').textContent =
      `${metadata.label} · ${metadata.metrics.length} metric · ${metadata.dimensions.length} dimensions · ` +
      `max ${metadata.guardrails.max_group_by_dimensions} group-by`;

    renderMetricSelect();
    renderDimensionChecks();
    renderFilterFields();
    renderPresets();
    renderFutureDimensions();
    renderSimulatedPreview();
  }

  function renderMetricSelect() {
    el('sem-metric').innerHTML = state.metrics.map((metric) => (
      `<option value="${esc(metric.key)}">${esc(metric.label)} — ${esc(metric.description)}</option>`
    )).join('');
    el('sem-metric').value = state.metric;
  }

  // Available dimensions come from metadata; the checkbox state comes from the
  // selection array. Keeping them separate is what makes a fresh page load show
  // the grand total with nothing checked.
  const availableDimensions = () => (state.metadata && state.metadata.dimensions) || [];
  const dimensionSpec = (key) => availableDimensions().find((item) => item.key === key);

  function renderDimensionChecks() {
    el('sem-dimensions').innerHTML = availableDimensions().map((dimension) => `
      <label>
        <input type="checkbox" data-sem-dimension="${esc(dimension.key)}"
          ${state.dimensions.includes(dimension.key) ? 'checked' : ''}>
        ${esc(dimension.label)}
      </label>
    `).join('');
  }

  function renderFilterFields() {
    const filterable = availableDimensions().filter((dimension) => dimension.filterable);
    el('sem-filters').innerHTML = filterable.map((dimension) => `
      <div class="sem-field">
        <span>${esc(dimension.label)}</span>
        <select class="ccg-select" data-sem-filter="${esc(dimension.key)}">
          <option value="">(all)</option>
        </select>
      </div>
    `).join('');
  }

  function renderPresets() {
    el('sem-presets').innerHTML = state.presets.map((preset) => `
      <button class="ccg-button ccg-button-secondary" type="button"
        data-sem-preset="${esc(preset.key)}"
        title="equivalent existing view: ${esc(preset.equivalent_existing_view)}">
        ${esc(preset.label)}
      </button>
    `).join('');
  }

  function renderFutureDimensions() {
    const future = (state.metadata && state.metadata.future_dimensions) || [];
    el('sem-future-dims').innerHTML = future.map((item) => `
      <tr>
        <td>${esc(item.key)}</td>
        <td><span class="sem-flag">${esc(item.status)}</span></td>
        <td>${esc(item.reason)}</td>
      </tr>
    `).join('') || '<tr><td colspan="3">—</td></tr>';
  }

  function renderSimulatedPreview() {
    // Purely local, clearly-labelled illustration. Not engine output.
    const rows = [
      ['SIMULATED Category A', 'BP Channel 1', 4200],
      ['SIMULATED Category A', 'BP Channel 2', 2600],
      ['SIMULATED Category B', 'BP Channel 1', 1800],
      ['SIMULATED Category B', 'BP Channel 2', 900],
    ];
    el('sem-sim-tbody').innerHTML = rows.map(([category, channel, qty]) => `
      <tr><td>${esc(category)}</td><td>${esc(channel)}</td><td class="sem-num">${fmt(qty)}</td></tr>
    `).join('');
  }

  async function loadFilterOptions() {
    const options = await apiFetch(`${OPTIONS_API}?batch_id=${encodeURIComponent(state.batchId)}`);
    state.options = options.options || {};
    document.querySelectorAll('[data-sem-filter]').forEach((select) => {
      const key = select.dataset.semFilter;
      // Look the spec up in the metadata, never in the group-by selection: the
      // selection holds dimension keys, not dimension objects.
      const spec = dimensionSpec(key);
      const values = state.options[key] || [];
      const mapped = values.map((option) => (
        `<option value="${esc(option.key === null ? '__null__' : option.key)}">${esc(option.label)}</option>`
      )).join('');
      // '' is "(all)" and is never sent as a filter. '__null__' selects the
      // unassigned snapshot value, and is only offered when the dimension can
      // actually be unassigned.
      const needsUnassigned = Boolean(spec && spec.nullable)
        && !values.some((option) => option.key === null);
      select.innerHTML = '<option value="">(all)</option>'
        + (needsUnassigned ? '<option value="__null__">未归属</option>' : '')
        + mapped;
    });
  }

  async function resolveBatch() {
    // Reuse the production dashboard context to learn the current batch id.
    const context = await apiFetch('/api/v1/sales/actual/dashboard/context');
    const batch = context.batch || context.current_batch || null;
    if (!batch || !batch.batch_id) {
      throw new Error('当前没有 Published batch，无法演示。\n'
        + 'synthetic demo 数据可能没有 seed，或本地 demo 数据库被重建过。');
    }
    state.batchId = batch.batch_id;
    el('sem-batch').textContent = `batch: ${batch.batch_id} · ${batch.snapshot_month || ''}`;
  }

  // ---------------------------------------------------------------------
  // query building + execution
  // ---------------------------------------------------------------------
  function buildRequest() {
    const filters = [];
    Object.entries(state.filters).forEach(([dimension, raw]) => {
      // "(all)" is the empty option value. It must never reach the API as "",
      // null, "all" or "(all)" — an unset filter simply is not sent.
      if (raw === '' || raw === undefined || raw === null) return;
      if (raw === 'all' || raw === '(all)') return;
      if (raw === '__null__') {
        filters.push({ dimension, operator: 'eq', value: null });
        return;
      }
      const spec = dimensionSpec(dimension);
      const isNumeric = spec && (spec.type === 'integer' || spec.type === 'decimal');
      filters.push({
        dimension,
        operator: 'in',
        values: [isNumeric ? Number(raw) : raw],
      });
    });

    const [field, direction] = state.sort.split(':');
    return {
      dataset: state.metadata.dataset,
      batch_id: state.batchId,
      metrics: [state.metric],
      // Only the dimensions the user actually ticked, and only real keys.
      dimensions: state.dimensions.filter((key) => typeof key === 'string' && key),
      filters,
      sort: [{ field, direction }],
      limit: 100,
    };
  }

  async function runQuery() {
    el('sem-error').hidden = true;

    if (!state.batchId) {
      // A null batch_id is the one payload the API must reject: sending it would
      // only produce an opaque 422. Report the real cause instead.
      showError('没有当前 Published batch，无法执行查询。\n'
        + 'query API 需要真实的 batch_id；请先把 synthetic demo 数据 seed 到本地 demo 数据库。');
      return;
    }

    if (state.dimensions.length > MAX_GROUP_BY) {
      showError(`最多 ${MAX_GROUP_BY} 个 Group By 维度`);
      return;
    }

    const request = buildRequest();
    el('sem-run').disabled = true;
    try {
      state.requestCount += 1;
      const result = await apiFetch(QUERY_API, {
        method: 'POST',
        body: JSON.stringify(request),
      });
      state.lastResult = result;
      renderInspector(request, result);
      renderTable(result);
      renderPlan(result);
    } catch (error) {
      showError(error.message);
    } finally {
      el('sem-run').disabled = false;
    }
  }

  function showError(message) {
    const box = el('sem-error');
    box.textContent = message;
    box.hidden = false;
  }

  // ---------------------------------------------------------------------
  // rendering
  // ---------------------------------------------------------------------
  function classifyView(dimensions) {
    const key = dimensions.join(',');
    const map = {
      'product': 'Product aggregation',
      'channel,product': 'Channel × Product',
      'salesrep,product': 'SalesRep × Product',
      'product,sku': 'Product × SKU (Product → SKU drilldown)',
    };
    return map[key] || 'NEW combination — no dedicated production view exists';
  }

  function filterSummary(request) {
    if (!request.filters.length) return '—';
    return request.filters.map((filter) => {
      const values = filter.values || [filter.value];
      return `${filter.dimension} ${filter.operator} [${values.map((v) => (v === null ? 'unassigned' : v)).join(', ')}]`;
    }).join(' AND ');
  }

  function renderInspector(request, result) {
    const metric = state.metrics.find((item) => item.key === state.metric) || { label: state.metric };
    const dimensionLabels = request.dimensions
      .map((key) => (dimensionSpec(key) || {}).label || key)
      .filter(Boolean);
    el('sem-inspector').innerHTML = `
      <dt>Dataset</dt><dd>${esc(result.dataset)} <span class="sem-note">(${esc(state.metadata.label)})</span></dd>
      <dt>Metric</dt><dd>${esc(metric.label)}</dd>
      <dt>Dimensions</dt><dd>${dimensionLabels.length ? esc(dimensionLabels.join(' + ')) : '(none — grand total)'}</dd>
      <dt>Filters</dt><dd>${esc(filterSummary(request))}</dd>
      <dt>Equivalent Existing View</dt><dd>${esc(classifyView(request.dimensions))}</dd>
      <dt>Total Sales Qty</dt><dd>${fmt(result.total_qty)}</dd>
      <dt>Grouped rows</dt><dd>${result.pagination.total_group_rows} (returned ${result.pagination.returned_rows})</dd>
      <dt>Execution endpoint</dt><dd>POST ${esc(QUERY_API)} <span class="sem-note">(requests: ${state.requestCount})</span></dd>
    `;
  }

  function renderTable(result) {
    const dimensions = result.dimensions || [];
    const metrics = result.metrics || [];

    const headers = [];
    dimensions.forEach((dimension) => {
      headers.push({ key: dimension.key, label: dimension.label, numeric: false });
      if (dimension.label_field) {
        headers.push({ key: dimension.label_field, label: `${dimension.label} 名称`, numeric: false });
      }
    });
    metrics.forEach((metric) => {
      headers.push({ key: metric.key, label: `${metric.label}${metric.unit ? ` (${metric.unit})` : ''}`, numeric: true });
    });

    el('sem-thead').innerHTML = `<tr>${headers.map((header) => (
      `<th class="${header.numeric ? 'sem-num' : ''}">${esc(header.label)}</th>`
    )).join('')}</tr>`;

    if (!result.items.length) {
      el('sem-tbody').innerHTML = `<tr><td colspan="${headers.length || 1}">没有匹配的数据</td></tr>`;
    } else {
      el('sem-tbody').innerHTML = result.items.map((item) => `
        <tr>${headers.map((header) => (
          `<td class="${header.numeric ? 'sem-num' : ''}">${
            header.numeric ? fmt(item[header.key]) : esc(item[header.key] === null ? '未归属' : item[header.key])
          }</td>`
        )).join('')}</tr>
      `).join('');
    }

    const metric = metrics[0] || { label: 'Sales Qty', key: 'actual_qty' };
    el('sem-result-title').textContent = `Result — ${metric.label}`;
    el('sem-result-meta').textContent =
      `Total ${fmt(result.total_qty)} · ${result.pagination.total_group_rows} grouped rows` +
      (result.pagination.truncated ? ` · truncated at ${result.pagination.limit}` : '');
  }

  function renderPlan(result) {
    el('sem-plan').textContent = JSON.stringify(result.plan, null, 2);
  }

  // ---------------------------------------------------------------------
  // events — delegated so dynamically rendered controls keep working
  // ---------------------------------------------------------------------
  function bindEvents() {
    document.addEventListener('click', (event) => {
      const tab = event.target.closest('[data-sem-view]');
      if (tab) {
        document.querySelectorAll('.sem-tab').forEach((item) => item.classList.remove('active'));
        tab.classList.add('active');
        const view = tab.dataset.semView;
        el('sem-view-explorer').hidden = view !== 'explorer';
        el('sem-view-architecture').hidden = view !== 'architecture';
        return;
      }

      const preset = event.target.closest('[data-sem-preset]');
      if (preset) {
        const chosen = state.presets.find((item) => item.key === preset.dataset.semPreset);
        if (!chosen) return;
        // A preset only edits Metric / Dimension selection, then calls the SAME api.
        state.metric = chosen.metric;
        state.dimensions = chosen.dimensions.slice();
        el('sem-metric').value = state.metric;
        renderDimensionChecks();
        runQuery();
        return;
      }

      if (event.target.closest('#sem-run')) {
        runQuery();
      }
    });

    document.addEventListener('change', (event) => {
      const dimensionBox = event.target.closest('[data-sem-dimension]');
      if (dimensionBox) {
        const key = dimensionBox.dataset.semDimension;
        if (dimensionBox.checked) {
          if (!state.dimensions.includes(key)) state.dimensions.push(key);
        } else {
          state.dimensions = state.dimensions.filter((item) => item !== key);
        }
        if (state.dimensions.length > MAX_GROUP_BY) {
          dimensionBox.checked = false;
          state.dimensions = state.dimensions.filter((item) => item !== key);
          showError(`最多 ${MAX_GROUP_BY} 个 Group By 维度`);
        }
        return;
      }

      if (event.target.id === 'sem-metric') {
        state.metric = event.target.value;
        return;
      }

      if (event.target.id === 'sem-sort') {
        state.sort = event.target.value;
        return;
      }

      const filterSelect = event.target.closest('[data-sem-filter]');
      if (filterSelect) {
        state.filters[filterSelect.dataset.semFilter] = filterSelect.value;
      }
    });
  }

  async function init() {
    bindEvents();
    try {
      // Verifies the stored bearer token and populates the shared currentUser the
      // design-system shell reads; redirects to /login when the session is gone.
      if (typeof requireLogin === 'function') {
        const user = await requireLogin();
        if (!user) return;
      }
      await loadMetadata();
    } catch (error) {
      showError(`初始化失败: ${error.message}`);
      el('sem-subtitle').textContent = 'Semantic metadata 初始化失败';
      return;
    }

    // Metadata is independent of the current batch: a missing Published batch is
    // a data problem, not a broken page, and it must not be reported as one.
    try {
      await resolveBatch();
      await loadFilterOptions();
      await runQuery();
    } catch (error) {
      el('sem-batch').textContent = 'batch: —';
      el('sem-run').disabled = true;
      showError(error.message);
    }
  }

  window.addEventListener('DOMContentLoaded', init);
})();
