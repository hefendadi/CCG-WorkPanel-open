/* Actual Sales V2 — lightweight analytics (TASK-ASV2-001).
 *
 * One page, one execution endpoint. Every global filter change and every product
 * selection issues the SAME request:
 *
 *     POST /api/v1/sales/actual/analytics/query
 *
 * The browser sends logical keys only (metric `actual_sales`, dimensions
 * `product` / `channel` / `salesrep` / `sales_date`). The server resolves each key
 * through the semantic registry whitelist; the page never names a physical
 * column, never writes a query statement and never receives one back.
 *
 * The metric label shown in the table header comes from the registry metadata
 * endpoint, so the page stays metadata-driven.
 *
 * The horizontal bar is rendered from the query result with plain CSS widths.
 * No charting library is loaded or required.
 */
const $ = id => document.getElementById(id);
const API = '/api/v1/sales/actual/analytics';
const CONTEXT_API = '/api/v1/sales/actual/dashboard/context';

const METRIC_KEY = 'actual_sales';
const DIM_PRODUCT = 'product';
const DIM_CHANNEL = 'channel';
const DIM_SALESREP = 'salesrep';
const DIM_SALES_DATE = 'sales_date';

const ALL = '';
const UNASSIGNED = '__null__';
const UNASSIGNED_LABEL = '未归属';
const ROW_LIMIT = 100;
const METRIC_FALLBACK = { key: 'actual_sales', label: '实际销量', unit: 'Pcs' };

const state = { batchId: null, metadata: null, options: {}, product: null, productLabel: '' };

/* The metric label / unit shown in the UI come from GET /metadata.  The literal
 * fallback only covers the moment before metadata arrives. */
function metric() {
  const metrics = (state.metadata && state.metadata.metrics) || [];
  return metrics.find(item => item.key === METRIC_KEY) || METRIC_FALLBACK;
}

/* Exact decimal formatting comes from the shared dashboard formatter, which never
 * rounds a quantity through Number. `qty` from common.js is only a fallback. */
function fmtQty(value) {
  const formatter = globalThis.salesDashboardFormat;
  return formatter && formatter.qty ? formatter.qty(value) : qty(value);
}

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
    const errors = detail.errors || detail.detail;
    let message = body && typeof body.detail === 'string' ? body.detail : undefined;
    if (Array.isArray(errors) && errors.length) message = errors.map(item => item.message || item.code).join('；');
    else if (detail.message) message = detail.message;
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
  box.className = 'ccg-state ccg-state-loading sales-analytics-state';
  $('state-icon').textContent = '…';
  $('state-title').textContent = title;
  $('state-detail').textContent = detail;
  $('page-retry').hidden = true;
  $('analytics-content').hidden = true;
}

function showLoadError(title, detail) {
  const box = $('page-state');
  box.hidden = false;
  box.className = 'ccg-state ccg-state-error sales-analytics-state';
  $('state-icon').textContent = '!';
  $('state-title').textContent = title;
  $('state-detail').textContent = detail;
  $('page-retry').hidden = false;
  $('analytics-content').hidden = true;
}

function showContent() {
  $('page-state').hidden = true;
  $('analytics-content').hidden = false;
}

/* ------------------------------------------------------------------ filters */

function optionValue(option) {
  return option.key === null ? UNASSIGNED : String(option.key);
}
function optionLabel(option) {
  return option.key === null ? UNASSIGNED_LABEL : option.label;
}

function fillSelect(element, placeholder, options) {
  const previous = element.value;
  const html = [`<option value="${ALL}">${esc(placeholder)}</option>`]
    .concat(options.map(option => `<option value="${esc(optionValue(option))}">${esc(optionLabel(option))}</option>`));
  element.innerHTML = html.join('');
  if (previous && options.some(option => optionValue(option) === previous)) element.value = previous;
}

function isSet(value) {
  return value !== '' && value !== undefined && value !== null && value !== 'all' && value !== '(all)';
}

function dimensionFilter(dimension, raw) {
  if (!isSet(raw)) return null;
  if (raw === UNASSIGNED) return { dimension, operator: 'eq', value: null };
  return { dimension, operator: 'in', values: [Number(raw)] };
}

function timeFilters() {
  const from = $('filter-from').value;
  const to = $('filter-to').value;
  if (!from && !to) return [];
  if (from && to && from > to) throw new Error('时间范围起点晚于终点，请重新选择。');
  if (from && to) return [{ dimension: DIM_SALES_DATE, operator: 'between', values: [from, to] }];
  if (from) return [{ dimension: DIM_SALES_DATE, operator: 'gte', value: from }];
  return [{ dimension: DIM_SALES_DATE, operator: 'lte', value: to }];
}

function globalFilters() {
  const filters = timeFilters();
  for (const [dimension, elementId] of [[DIM_PRODUCT, 'filter-product'], [DIM_CHANNEL, 'filter-channel'], [DIM_SALESREP, 'filter-salesrep']]) {
    const filter = dimensionFilter(dimension, $(elementId).value);
    if (filter) filters.push(filter);
  }
  return filters;
}

/* ------------------------------------------------------------------- queries */

function request(filters, dimensions, extraFilters = []) {
  return {
    dataset: 'sales_actual',
    batch_id: state.batchId,
    metrics: [METRIC_KEY],
    dimensions,
    filters: filters.concat(extraFilters),
    sort: [{ field: METRIC_KEY, direction: 'desc' }],
    limit: ROW_LIMIT,
  };
}

async function query(body) {
  if (!state.batchId) throw new Error('没有当前已发布批次（Published Batch），无法查询。');
  return apiFetch('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/* ---------------------------------------------------------------- rendering */

function renderProducts(result) {
  const items = result.items || [];
  const active = metric();
  const head = `<thead><tr><th>商品</th><th class="number">${esc(active.label)} (${esc(active.unit || 'Pcs')})</th></tr></thead>`;
  const body = items.map(item => {
    const selected = state.product !== null && String(item[DIM_PRODUCT]) === String(state.product);
    return `<tr class="sales-analytics-product-row${selected ? ' is-selected' : ''}"`
      + ` data-product-id="${esc(item[DIM_PRODUCT])}" data-product-name="${esc(item.product_name || UNASSIGNED_LABEL)}"`
      + ` tabindex="0" role="button" aria-pressed="${selected ? 'true' : 'false'}">`
      + `<td>${esc(item.product_name || UNASSIGNED_LABEL)}</td>`
      + `<td class="number">${esc(fmtQty(item[METRIC_KEY]))}</td></tr>`;
  }).join('');
  $('product-table').innerHTML = head + '<tbody>' + (body || '<tr><td colspan="2" class="empty">当前筛选条件下没有数据</td></tr>') + '</tbody>';

  if (state.product !== null && !items.some(item => String(item[DIM_PRODUCT]) === String(state.product))) {
    clearChannelSelection();
  }
}

function renderChannelBars(result) {
  const items = (result.items || []).filter(item => item[METRIC_KEY] !== null);
  const values = items.map(item => Number(item[METRIC_KEY]));
  const max = values.reduce((best, value) => (Number.isFinite(value) && value > best ? value : best), 0);
  $('channel-bars').innerHTML = items.map(item => {
    const value = Number(item[METRIC_KEY]);
    const ratio = max > 0 && Number.isFinite(value) ? Math.max(value / max, 0.02) : 0;
    const width = (ratio * 100).toFixed(2);
    const unassigned = item.channel_name === UNASSIGNED_LABEL || item[DIM_CHANNEL] === null;
    return `<div class="sales-analytics-bar-row${unassigned ? ' is-unassigned' : ''}" role="listitem">`
      + `<span class="sales-analytics-bar-label" title="${esc(item.channel_name || UNASSIGNED_LABEL)}">${esc(item.channel_name || UNASSIGNED_LABEL)}</span>`
      + `<span class="sales-analytics-bar-track"><span class="sales-analytics-bar-fill" style="width:${width}%"></span></span>`
      + `<span class="sales-analytics-bar-value">${esc(fmtQty(item[METRIC_KEY]))}</span></div>`;
  }).join('');
  $('channel-empty').hidden = items.length > 0;
  $('channel-empty-title').textContent = items.length ? '' : '当前筛选条件下没有 BP渠道 数据';
}

function clearChannelSelection() {
  state.product = null;
  state.productLabel = '';
  $('channel-bars').innerHTML = '';
  $('channel-empty').hidden = false;
  $('channel-empty-title').textContent = '尚未选择商品';
  $('channel-subtitle').textContent = '请先在上方表格选择一个商品。';
}

/* --------------------------------------------------------------------- load */

async function loadProducts() {
  clearMessage();
  const result = await query(request(globalFilters(), [DIM_PRODUCT]));
  renderProducts(result);
}

async function selectProduct(productId, productLabel) {
  state.product = productId;
  state.productLabel = productLabel;
  $('channel-subtitle').textContent = `商品：${productLabel} · 按 BP渠道 拆分${metric().label}`;
  for (const row of document.querySelectorAll('.sales-analytics-product-row')) {
    const active = row.dataset.productId === String(productId);
    row.classList.toggle('is-selected', active);
    row.setAttribute('aria-pressed', String(active));
  }
  const result = await query(request(globalFilters(), [DIM_CHANNEL], [
    { dimension: DIM_PRODUCT, operator: 'eq', value: Number(productId) },
  ]));
  renderChannelBars(result);
}

async function loadContext() {
  const response = await authFetch(CONTEXT_API);
  if (!response.ok) throw new Error(`无法读取当前批次（HTTP ${response.status}）`);
  const context = await response.json();
  state.batchId = context.current_batch ? context.current_batch.batch_id : null;
  const month = String(context.snapshot_month || '').slice(0, 7);
  const end = context.data_end_date || '—';
  $('snapshot-note').textContent = `快照月份 ${month || '—'} · 数据截至 ${end} · 商品 / BP渠道 / 营业员 均取 Publish 时冻结的归属`;
}

async function loadMetadata() {
  state.metadata = await apiFetch('/metadata');
}

async function loadOptions() {
  const query_string = `?batch_id=${encodeURIComponent(state.batchId)}&dimensions=${DIM_PRODUCT},${DIM_CHANNEL},${DIM_SALESREP}`;
  const body = await apiFetch(`/filter-options${query_string}`);
  state.options = body.options || {};
  fillSelect($('filter-product'), '全部商品', state.options[DIM_PRODUCT] || []);
  fillSelect($('filter-channel'), '全部 BP渠道', state.options[DIM_CHANNEL] || []);
  fillSelect($('filter-salesrep'), '全部营业员', state.options[DIM_SALESREP] || []);
}

async function init() {
  showLoading('正在加载轻量分析', '正在读取当前已发布快照…');
  try {
    await loadContext();
    if (!state.batchId) throw new Error('没有当前已发布批次（Published Batch）。请先在数据维护中发布销售实绩。');
    await loadMetadata();
    await loadOptions();
    await loadProducts();
    showContent();
  } catch (error) {
    showLoadError('轻量分析加载失败', error.message);
  }
}

/* ------------------------------------------------------------------- events */

$('filter-product').addEventListener('change', () => { loadProducts().catch(error => showMessage(error.message)); });
$('filter-channel').addEventListener('change', () => { loadProducts().catch(error => showMessage(error.message)); });
$('filter-salesrep').addEventListener('change', () => { loadProducts().catch(error => showMessage(error.message)); });
$('filter-from').addEventListener('change', () => { loadProducts().catch(error => showMessage(error.message)); });
$('filter-to').addEventListener('change', () => { loadProducts().catch(error => showMessage(error.message)); });
$('clear-filters').addEventListener('click', () => {
  for (const elementId of ['filter-from', 'filter-to', 'filter-product', 'filter-channel', 'filter-salesrep']) $(elementId).value = ALL;
  clearChannelSelection();
  loadProducts().catch(error => showMessage(error.message));
});
$('product-table').addEventListener('click', event => {
  const row = event.target.closest('.sales-analytics-product-row');
  if (row) selectProduct(row.dataset.productId, row.dataset.productName).catch(error => showMessage(error.message));
});
$('product-table').addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const row = event.target.closest('.sales-analytics-product-row');
  if (!row) return;
  event.preventDefault();
  selectProduct(row.dataset.productId, row.dataset.productName).catch(error => showMessage(error.message));
});
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
