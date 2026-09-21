/* Sales Pivot Workbench (PIVOT-UX-DEMO-V2 / CR02).
 *
 * One page, one execution endpoint. The user builds the analysis from the field
 * catalog; nothing about the result shape is hard-coded:
 *
 *     POST /api/v1/sales/actual/pivot/query
 *
 * CR02 adds the Excel-pivot presentation layer on top of the same endpoint:
 * hierarchical rows (parent → children), optional 小计 per dimension, a 合计 row,
 * an MTD / custom business-time selector, and three pre-filled configurations
 * (presets) that only set state — never a fixed page.
 *
 * The browser sends logical field keys only. The server resolves each key through
 * the pivot catalog whitelist, so the page never names a physical column and never
 * receives one back. The horizontal bar is plain CSS; no charting library is used.
 */
const $ = id => document.getElementById(id);
const API = '/api/v1/sales/actual/pivot';
const CONTEXT_API = '/api/v1/sales/actual/dashboard/context';
const METRIC_KEY = 'actual_sales';
const MAX_DIMENSIONS = 4;
const ROW_LIMIT = 500;
const UNASSIGNED_LABEL = '未归属';

const state = {
  batchId: null,
  fields: {},
  groups: [],
  presets: [],
  picked: [],
  subtotals: [],
  filters: [],
  options: {},
  display: 'table',
  timeMode: 'all',
  timeStart: '',
  timeEnd: '',
  grandTotal: true,
  labelOverrides: {},
  labels: {},
  result: null,
  seq: 0,
};

/* --------------------------------------------------------------- formatting */

/* 千分位、整数：后端返回精确小数字符串，前端只做展示格式化。 */
function fmtQty(value) {
  const text = String(value === null || value === undefined ? '' : value).trim();
  if (!text) return '—';
  const negative = text.startsWith('-');
  const body = negative ? text.slice(1) : text;
  const parts = body.split('.');
  const intPart = parts[0] || '0';
  const frac = (parts[1] || '').replace(/0+$/, '');
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}${grouped}${frac ? `.${frac}` : ''}`;
}

function metricLabel() {
  return state.fields[METRIC_KEY] && state.fields[METRIC_KEY].label
    ? state.fields[METRIC_KEY].label
    : (state.labels.metric || '实际销量');
}

function dimensionLabel(key) {
  return state.labelOverrides[key]
    || (state.fields[key] && state.fields[key].label)
    || key;
}

/* ------------------------------------------------------------------ network */

async function apiFetch(path, options = {}) {
  const response = await authFetch(API + path, options);
  const contentType = (response.headers.get('Content-Type') || '').split(';', 1)[0].trim().toLowerCase();
  const isJson = contentType === 'application/json' || contentType.endsWith('+json');
  let body = {};
  if (isJson) {
    try { body = await response.json(); }
    catch (_error) { if (response.ok) throw new Error('服务器响应格式异常'); }
  }
  if (!response.ok) {
    const detail = body && typeof body.detail === 'object' && body.detail ? body.detail : {};
    let message = body && typeof body.detail === 'string' ? body.detail : undefined;
    if (detail.message) message = detail.message;
    else if (detail.code) message = detail.code;
    if (response.status === 401) message = '登录状态已失效，请重新登录。';
    else if (response.status === 403) message = '没有销售实绩的查看权限。';
    throw new Error(message || `请求未完成（HTTP ${response.status}）`);
  }
  return body;
}

function showMessage(message) {
  $('message-copy').textContent = message;
  $('message').hidden = false;
}
function clearMessage() { $('message').hidden = true; $('message-copy').textContent = ''; }

function showLoading(title, detail) {
  const box = $('page-state');
  box.hidden = false;
  box.className = 'ccg-state ccg-state-loading sales-pivot-state';
  $('state-icon').textContent = '…';
  $('state-title').textContent = title;
  $('state-detail').textContent = detail;
  $('page-retry').hidden = true;
  $('workbench').hidden = true;
}
function showLoadError(title, detail) {
  const box = $('page-state');
  box.hidden = false;
  box.className = 'ccg-state ccg-state-error sales-pivot-state';
  $('state-icon').textContent = '!';
  $('state-title').textContent = title;
  $('state-detail').textContent = detail;
  $('page-retry').hidden = false;
  $('workbench').hidden = true;
}
function showWorkbench() { $('page-state').hidden = true; $('workbench').hidden = false; }

/* ------------------------------------------------------------------ catalog */

function renderCatalog() {
  const html = state.groups.map(group => {
    const items = group.fields.map(field => {
      const picked = state.picked.includes(field.key);
      const badge = field.has_sample_data === false
        ? '<span class="sales-pivot-field-badge">当前 Sample 无数据</span>' : '';
      return `<button type="button" class="sales-pivot-field${picked ? ' is-picked' : ''}"`
        + ` data-field="${esc(field.key)}" title="${esc(field.label)}">`
        + `<span>${esc(field.label)}</span>${badge}</button>`;
    }).join('');
    return `<section><h3 class="sales-pivot-group-title">${esc(group.label)}</h3>`
      + `<div class="sales-pivot-field-list">${items}</div></section>`;
  }).join('');
  $('field-groups').innerHTML = html;
  for (const button of document.querySelectorAll('.sales-pivot-field')) {
    button.addEventListener('click', () => addField(button.dataset.field));
  }
}

function renderFilterPicker() {
  const groups = state.groups.map(group => {
    const items = group.fields.filter(field => field.filterable).map(field =>
      `<option value="${esc(field.key)}">${esc(field.label)}</option>`).join('');
    return items ? `<optgroup label="${esc(group.label)}">${items}</optgroup>` : '';
  }).join('');
  $('filter-field-picker').innerHTML = `<option value="">选择要筛选的字段…</option>${groups}`;
}

/* ------------------------------------------------------------------ presets */

function renderPresets() {
  $('preset-buttons').innerHTML = state.presets.map(preset =>
    `<button type="button" class="ccg-button ccg-button-secondary sales-pivot-preset"`
    + ` data-preset="${esc(preset.key)}" title="${esc(preset.description)}"`
    + `${preset.available === false ? ' disabled aria-disabled="true"' : ''}>`
    + `${esc(preset.label)}</button>`).join('');
  for (const button of document.querySelectorAll('.sales-pivot-preset')) {
    if (button.disabled) continue;
    button.addEventListener('click', () => applyPreset(button.dataset.preset));
  }
  const blocked = state.presets.filter(preset => preset.available === false);
  const note = blocked.map(preset => `${preset.label}：${preset.notice}`).join(' ');
  $('preset-blocked').hidden = blocked.length === 0;
  $('preset-blocked').textContent = note;
}

function showPresetNotice(text) {
  $('preset-notice').hidden = !text;
  $('preset-notice').textContent = text || '';
}

async function applyPreset(key) {
  const preset = state.presets.find(item => item.key === key);
  if (!preset || preset.available === false) return;   // blocked presets never execute
  clearMessage();
  const config = preset.config || {};
  state.picked = (config.dimensions || []).filter(item => state.fields[item]);
  state.subtotals = (config.subtotals || []).filter(item => state.picked.includes(item));
  state.grandTotal = config.grand_total !== false;
  $('grand-total-toggle').checked = state.grandTotal;
  state.labelOverrides = Object.assign({}, config.label_overrides || {});
  state.timeMode = (config.time && config.time.mode) || 'all';
  state.timeStart = (config.time && config.time.start) || '';
  state.timeEnd = (config.time && config.time.end) || '';
  await applyPresetFilters(config.filters || []);
  applyTimeModeToUI();
  renderCatalog();
  renderPicked();
  showPresetNotice(preset.notice || preset.description || '');
  await execute();
}

async function applyPresetFilters(filters) {
  state.filters = [];
  for (const item of filters) {
    const spec = state.fields[item.field];
    if (!spec || !spec.filterable) continue;
    const row = { id: ++state.seq, key: item.field, spec, from: '', to: '',
                  values: (item.values || []).map(value => String(value)), search: '' };
    if (spec.type !== 'date' && spec.type !== 'month') {
      try { await loadOptions(item.field); } catch (error) { showMessage(error.message); }
    }
    state.filters.push(row);
  }
  renderFilters();
}

/* ---------------------------------------------------------------- dimensions */

function addField(key) {
  const field = state.fields[key];
  if (!field || !field.groupable) return;
  if (state.picked.includes(key)) { showMessage(`「${field.label}」已经在分析维度中。`); return; }
  if (state.picked.length >= MAX_DIMENSIONS) {
    showMessage(`分析维度最多 ${MAX_DIMENSIONS} 个，请先移除一个。`);
    return;
  }
  clearMessage();
  state.picked.push(key);
  state.labelOverrides = {};
  renderCatalog();
  renderPicked();
}

function moveField(key, delta) {
  const index = state.picked.indexOf(key);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= state.picked.length) return;
  state.picked.splice(index, 1);
  state.picked.splice(target, 0, key);
  state.labelOverrides = {};
  renderPicked();
  renderCatalog();
}

function removeField(key) {
  state.picked = state.picked.filter(item => item !== key);
  state.subtotals = state.subtotals.filter(item => item !== key);
  state.labelOverrides = {};
  renderPicked();
  renderCatalog();
}

function toggleSubtotal(key, enabled) {
  if (enabled && !state.subtotals.includes(key)) state.subtotals.push(key);
  if (!enabled) state.subtotals = state.subtotals.filter(item => item !== key);
  renderPicked();
}

function renderPicked() {
  const list = $('picked-list');
  list.innerHTML = state.picked.map((key, index) => {
    const label = dimensionLabel(key);
    const subtotal = state.subtotals.includes(key);
    return `<li class="sales-pivot-dim" data-field="${esc(key)}">`
      + `<span class="sales-pivot-dim-order">${index + 1}</span>`
      + `<span>${esc(label)}</span>`
      + `<label class="sales-pivot-dim-subtotal" title="在该层级生成小计行">`
      + `<input type="checkbox" data-subtotal="${esc(key)}"${subtotal ? ' checked' : ''}> 小计</label>`
      + `<span class="sales-pivot-dim-actions">`
      + `<button type="button" class="sales-pivot-icon-button" data-move="-1" title="上移"${index === 0 ? ' disabled' : ''}>↑</button>`
      + `<button type="button" class="sales-pivot-icon-button" data-move="1" title="下移"${index === state.picked.length - 1 ? ' disabled' : ''}>↓</button>`
      + `<button type="button" class="sales-pivot-icon-button" data-remove="1" title="移除">×</button>`
      + '</span></li>';
  }).join('');
  $('picked-empty').hidden = state.picked.length > 0;
  for (const button of list.querySelectorAll('[data-move]')) {
    button.addEventListener('click', () => {
      const key = button.closest('.sales-pivot-dim').dataset.field;
      moveField(key, Number(button.dataset.move));
    });
  }
  for (const button of list.querySelectorAll('[data-remove]')) {
    button.addEventListener('click', () => {
      removeField(button.closest('.sales-pivot-dim').dataset.field);
    });
  }
  for (const box of list.querySelectorAll('[data-subtotal]')) {
    box.addEventListener('change', () => toggleSubtotal(box.dataset.subtotal, box.checked));
  }
  updateDisplayOptions();
}

function updateDisplayOptions() {
  const single = state.picked.length === 1;
  $('display-bar-option').disabled = !single;
  $('bar-hint').hidden = single;
  if (!single && state.display === 'bar') {
    state.display = 'table';
    document.querySelector('input[name="sales-pivot-display"][value="table"]').checked = true;
  }
}

/* ---------------------------------------------------------------- time mode */

function applyTimeModeToUI() {
  for (const radio of document.querySelectorAll('input[name="sales-pivot-time"]')) {
    radio.checked = radio.value === state.timeMode;
  }
  $('time-custom').hidden = state.timeMode !== 'custom';
  $('time-start').value = state.timeStart || '';
  $('time-end').value = state.timeEnd || '';
}

/* ------------------------------------------------------------------- filters */

async function loadOptions(key) {
  if (state.options[key]) return state.options[key];
  const body = await apiFetch(`/filter-options?batch_id=${encodeURIComponent(state.batchId)}&fields=${encodeURIComponent(key)}`);
  state.options[key] = (body.options && body.options[key]) || [];
  return state.options[key];
}

async function addFilter() {
  const key = $('filter-field-picker').value;
  if (!key) { showMessage('请先选择要筛选的字段。'); return; }
  if (state.filters.some(item => item.key === key)) { showMessage('该字段已经添加过筛选条件。'); return; }
  const field = state.fields[key];
  if (!field || !field.filterable) return;
  clearMessage();
  const row = { id: ++state.seq, key, spec: field, from: '', to: '', values: [] };
  state.filters.push(row);
  try {
    if (field.type !== 'date' && field.type !== 'month') await loadOptions(key);
  } catch (error) {
    showMessage(error.message);
  }
  renderFilters();
}

function renderFilters() {
  const container = $('filter-rows');
  container.innerHTML = state.filters.map(row => {
    const spec = row.spec;
    let control;
    if (spec.type === 'date' || spec.type === 'month') {
      control = `<label class="sales-pivot-filter-values">时间范围`
        + `<input type="date" class="ccg-input" data-from="${row.id}" value="${esc(row.from)}">`
        + `–<input type="date" class="ccg-input" data-to="${row.id}" value="${esc(row.to)}"></label>`;
    } else if (spec.type === 'boolean') {
      const options = (state.options[row.key] || []).map(option => {
        const value = option.key === null ? '__null__' : String(option.key);
        const selected = row.values.map(String).includes(value) ? ' selected' : '';
        return `<option value="${esc(value)}"${selected}>${esc(option.label)}</option>`;
      }).join('');
      control = `<select class="ccg-select" data-values="${row.id}" size="1">${options}</select>`;
    } else {
      control = multiSelectMarkup(row);
    }
    return `<div class="sales-pivot-filter" data-filter="${row.id}">`
      + `<div class="sales-pivot-filter-field">${esc(spec.label)}</div>`
      + `<div class="sales-pivot-filter-values">${control}</div>`
      + `<button type="button" class="sales-pivot-icon-button" data-drop="${row.id}" title="移除">×</button></div>`;
  }).join('');
  $('filter-empty').hidden = state.filters.length > 0;

  for (const input of container.querySelectorAll('[data-from]')) {
    input.addEventListener('change', () => { findFilter(input.dataset.from).from = input.value; });
  }
  for (const input of container.querySelectorAll('[data-to]')) {
    input.addEventListener('change', () => { findFilter(input.dataset.to).to = input.value; });
  }
  for (const select of container.querySelectorAll('[data-values]')) {
    select.addEventListener('change', () => {
      const row = findFilter(select.dataset.values);
      row.values = Array.from(select.selectedOptions).map(option => option.value);
    });
  }
  for (const button of container.querySelectorAll('[data-drop]')) {
    button.addEventListener('click', () => {
      state.filters = state.filters.filter(item => String(item.id) !== String(button.dataset.drop));
      renderFilters();
    });
  }
  bindMultiSelects(container);
}

/* -- lightweight multi-select popover (no dependency, no Ctrl/Cmd needed) -- */

function multiSelectMarkup(row) {
  const options = state.options[row.key] || [];
  const selected = new Set(row.values.map(String));
  const items = options.map(option => {
    const value = option.key === null ? '__null__' : String(option.key);
    const label = option.label;
    const checked = selected.has(value) ? ' checked' : '';
    return `<label class="sales-pivot-ms-option" data-option="${esc(value)}" data-label="${esc(label.toLowerCase())}">`
      + `<input type="checkbox" data-ms-value="${row.id}" value="${esc(value)}"${checked}>`
      + `<span>${esc(label)}</span></label>`;
  }).join('');
  return `<div class="sales-pivot-ms" data-ms="${row.id}">`
    + `<button type="button" class="sales-pivot-ms-trigger" data-ms-toggle="${row.id}">`
    + `${esc(selectedLabel(row.values.length))} ▾</button>`
    + `<div class="sales-pivot-ms-panel" data-ms-panel="${row.id}" hidden>`
    + `<input type="search" class="ccg-input sales-pivot-ms-search" data-ms-search="${row.id}" placeholder="搜索选项…" value="${esc(row.search || '')}">`
    + `<div class="sales-pivot-ms-actions">`
    + `<button type="button" class="ccg-button ccg-button-secondary" data-ms-all="${row.id}">全选</button>`
    + `<button type="button" class="ccg-button ccg-button-secondary" data-ms-clear="${row.id}">清空</button>`
    + `</div><div class="sales-pivot-ms-list">${items || '<p class="sales-pivot-ms-empty">没有可选值</p>'}</div></div></div>`;
}

function selectedLabel(count) {
  return `已选 ${count} 项`;
}

function bindMultiSelects(container) {
  for (const trigger of container.querySelectorAll('[data-ms-toggle]')) {
    trigger.addEventListener('click', event => {
      event.stopPropagation();
      const panel = container.querySelector(`[data-ms-panel="${trigger.dataset.msToggle}"]`);
      const wasHidden = panel.hidden;
      for (const other of container.querySelectorAll('[data-ms-panel]')) other.hidden = true;
      panel.hidden = !wasHidden;
    });
  }
  for (const panel of container.querySelectorAll('[data-ms-panel]')) {
    panel.addEventListener('click', event => event.stopPropagation());
  }
  for (const input of container.querySelectorAll('[data-ms-search]')) {
    input.addEventListener('input', () => {
      const row = findFilter(input.dataset.msSearch);
      row.search = input.value;
      const term = input.value.trim().toLowerCase();
      const panel = container.querySelector(`[data-ms-panel="${input.dataset.msSearch}"]`);
      for (const option of panel.querySelectorAll('[data-option]')) {
        option.hidden = Boolean(term) && !option.dataset.label.includes(term);
      }
    });
  }
  for (const button of container.querySelectorAll('[data-ms-all]')) {
    button.addEventListener('click', () => {
      const panel = container.querySelector(`[data-ms-panel="${button.dataset.msAll}"]`);
      for (const box of panel.querySelectorAll('[data-ms-value]')) {
        if (!box.closest('[data-option]').hidden) box.checked = true;
      }
      syncMultiSelect(button.dataset.msAll);
    });
  }
  for (const button of container.querySelectorAll('[data-ms-clear]')) {
    button.addEventListener('click', () => {
      const panel = container.querySelector(`[data-ms-panel="${button.dataset.msClear}"]`);
      for (const box of panel.querySelectorAll('[data-ms-value]')) box.checked = false;
      syncMultiSelect(button.dataset.msClear);
    });
  }
  for (const box of container.querySelectorAll('[data-ms-value]')) {
    box.addEventListener('change', () => syncMultiSelect(box.dataset.msValue));
  }
}

function syncMultiSelect(id) {
  const row = findFilter(id);
  const container = $('filter-rows');
  const panel = container.querySelector(`[data-ms-panel="${id}"]`);
  const boxes = Array.from(panel.querySelectorAll('[data-ms-value]'));
  row.values = boxes.filter(box => box.checked).map(box => box.value);
  const trigger = container.querySelector(`[data-ms-toggle="${id}"]`);
  trigger.textContent = `${selectedLabel(row.values.length)} ▾`;
}

document.addEventListener('click', () => {
  for (const panel of document.querySelectorAll('[data-ms-panel]')) panel.hidden = true;
});

function findFilter(id) {
  return state.filters.find(item => String(item.id) === String(id));
}

function collectFilters() {
  const filters = [];
  for (const row of state.filters) {
    const spec = row.spec;
    if (spec.type === 'date' || spec.type === 'month') {
      if (row.from && row.to) filters.push({ field: row.key, operator: 'between', values: [row.from, row.to] });
      else if (row.from) filters.push({ field: row.key, operator: 'gte', value: row.from });
      else if (row.to) filters.push({ field: row.key, operator: 'lte', value: row.to });
      continue;
    }
    if (!row.values.length) continue;
    const numeric = spec.type === 'reference' || spec.type === 'number';
    const values = row.values.map(value => {
      if (value === '__null__') return null;
      return numeric ? Number(value) : value;
    });
    filters.push({ field: row.key, operator: 'in', values });
  }
  return filters;
}

function collectTime() {
  if (state.timeMode === 'custom') {
    const body = { mode: 'custom' };
    if (state.timeStart) body.start = state.timeStart;
    if (state.timeEnd) body.end = state.timeEnd;
    return body;
  }
  return { mode: state.timeMode };
}

/* ------------------------------------------------------------------- results */

function resultTable(result) {
  const dimensions = state.picked;
  const span = Math.max(dimensions.length, 1);
  const head = '<thead><tr>' + result.columns.map(column => {
    const label = column.kind === 'metric' ? column.label : dimensionLabel(column.key);
    return `<th${column.kind === 'metric' ? ' class="number"' : ''}>${esc(label)}</th>`;
  }).join('') + '</tr></thead>';

  /* Tabular form: leaf rows carry the path with repeated parent values blanked
     out by the server; only 小计 and 合计 render an aggregate. */
  const body = (result.rows || []).map(row => {
    if (row.kind === 'subtotal' || row.kind === 'total') {
      const label = row.label || (row.kind === 'total' ? '合计' : '');
      return `<tr class="sales-pivot-row is-${row.kind}">`
        + `<td class="sales-pivot-span" colspan="${span}">${esc(label)}</td>`
        + `<td class="number sales-pivot-metric-cell">${esc(fmtQty(row.metric))}</td></tr>`;
    }
    const cells = (row.cells || []).map(cell => `<td>${esc(cell || '')}</td>`).join('');
    return `<tr class="sales-pivot-row is-leaf">${cells}`
      + `<td class="number sales-pivot-metric-cell">${esc(fmtQty(row.metric))}</td></tr>`;
  }).join('');

  $('result-table').innerHTML = head + '<tbody>' + body + '</tbody>';
}

function resultBars(result) {
  const dimension = state.picked[0];
  const items = (result.items || []).filter(item => item[METRIC_KEY] !== null);
  const values = items.map(item => Number(item[METRIC_KEY]));
  const max = values.reduce((best, value) => (Number.isFinite(value) && value > best ? value : best), 0);
  $('result-bars').innerHTML = items.map(item => {
    const value = Number(item[METRIC_KEY]);
    const ratio = max > 0 && Number.isFinite(value) ? Math.max(value / max, 0.02) : 0;
    const width = (ratio * 100).toFixed(2);
    const label = item[dimension] || UNASSIGNED_LABEL;
    const unassigned = label === UNASSIGNED_LABEL;
    return `<div class="sales-pivot-bar-row${unassigned ? ' is-unassigned' : ''}" role="listitem">`
      + `<span class="sales-pivot-bar-label" title="${esc(label)}">${esc(label)}</span>`
      + `<span class="sales-pivot-bar-track"><span class="sales-pivot-bar-fill" style="width:${width}%"></span></span>`
      + `<span class="sales-pivot-bar-value">${esc(fmtQty(item[METRIC_KEY]))}</span></div>`;
  }).join('');
}

function renderResult() {
  const result = state.result;
  const empty = $('result-empty');
  const wrap = $('result-table-wrap');
  const bars = $('result-bars');
  if (!result) {
    empty.hidden = false;
    wrap.hidden = true;
    bars.hidden = true;
    return;
  }
  const leaves = (result.rows || []).filter(row => row.kind === 'leaf').length;
  const subtotals = (result.totals && result.totals.subtotals) || [];
  const timeLabel = (result.time && result.time.label) || '全部时间';
  $('result-meta').textContent = `${timeLabel} · ${state.picked.length} 个分析维度 · ${leaves} 行明细`
    + `${subtotals.length ? ` · ${subtotals.length} 个小计` : ''}`
    + `${result.totals && result.totals.grand_total_enabled ? ' · 含合计' : ''}`;

  if (!state.picked.length && !(result.rows || []).length) {
    empty.hidden = false;
    wrap.hidden = true;
    bars.hidden = true;
    $('result-empty-title').textContent = '当前条件下没有数据';
    return;
  }
  empty.hidden = true;

  const useBar = state.display === 'bar' && result.display && result.display.bar_supported;
  wrap.hidden = useBar;
  bars.hidden = !useBar;
  if (useBar) resultBars(result);
  else resultTable(result);
}

async function execute() {
  if (!state.batchId) { showMessage('没有当前已发布批次（Published Batch），无法执行分析。'); return; }
  if (state.timeMode === 'custom' && !state.timeStart && !state.timeEnd) {
    showMessage('自定义日期口径至少需要一个开始或结束日期。');
    return;
  }
  clearMessage();
  $('run-analysis').disabled = true;
  try {
    const body = {
      batch_id: state.batchId,
      dimensions: state.picked,
      metrics: [METRIC_KEY],
      filters: collectFilters(),
      sort: [{ field: METRIC_KEY, direction: 'desc' }],
      time: collectTime(),
      subtotals: state.subtotals,
      grand_total: state.grandTotal,
      limit: ROW_LIMIT,
    };
    state.result = await apiFetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    renderResult();
  } catch (error) {
    showMessage(error.message);
  } finally {
    $('run-analysis').disabled = false;
  }
}

/* --------------------------------------------------------------------- load */

async function loadContext() {
  const response = await authFetch(CONTEXT_API);
  if (!response.ok) throw new Error(`无法读取当前批次（HTTP ${response.status}）`);
  const context = await response.json();
  state.batchId = context.current_batch ? context.current_batch.batch_id : null;
  const month = String(context.snapshot_month || '').slice(0, 7);
  const end = context.data_end_date || '—';
  $('snapshot-note').textContent = `快照月份 ${month || '—'} · 数据截至 ${end} · 分析字段覆盖 商品 / 客户 / 渠道 / 营业 / 时间`;
}

async function loadCatalog() {
  const query = state.batchId ? `?batch_id=${encodeURIComponent(state.batchId)}` : '';
  const catalog = await apiFetch(`/fields${query}`);
  state.fields = {};
  for (const field of catalog.fields || []) state.fields[field.key] = field;
  state.groups = catalog.groups || [];
  state.presets = catalog.presets || [];
  state.labels = { metric: catalog.metric && catalog.metric.label };
  if (state.labels.metric) $('metric-label').textContent = state.labels.metric;
  renderCatalog();
  renderFilterPicker();
  renderPresets();
  renderPicked();
  renderFilters();
}

async function init() {
  showLoading('正在加载分析字段', '正在读取当前已发布快照…');
  try {
    await loadContext();
    if (!state.batchId) throw new Error('没有当前已发布批次（Published Batch）。请先在数据维护中发布销售实绩。');
    await loadCatalog();
    showWorkbench();
  } catch (error) {
    showLoadError('分析工作台加载失败', error.message);
  }
}

/* ------------------------------------------------------------------- events */

$('add-filter').addEventListener('click', () => { addFilter(); });
$('run-analysis').addEventListener('click', () => { execute(); });
for (const radio of document.querySelectorAll('input[name="sales-pivot-display"]')) {
  radio.addEventListener('change', () => {
    state.display = radio.value;
    if (state.result) renderResult();
  });
}
for (const radio of document.querySelectorAll('input[name="sales-pivot-time"]')) {
  radio.addEventListener('change', () => {
    state.timeMode = radio.value;
    applyTimeModeToUI();
  });
}
$('time-start').addEventListener('change', () => { state.timeStart = $('time-start').value; });
$('time-end').addEventListener('change', () => { state.timeEnd = $('time-end').value; });
$('grand-total-toggle').addEventListener('change', () => { state.grandTotal = $('grand-total-toggle').checked; });
$('message-dismiss').addEventListener('click', clearMessage);
$('page-retry').addEventListener('click', () => { init(); });

(async () => {
  if (!await requireLogin()) return;
  $('user').textContent = currentUser.username || '';
  if (!hasPermission('sales_actual', 'VIEW')) {
    showLoadError('没有权限', '当前账号没有销售实绩的查看权限。');
    return;
  }
  await init();
})();
