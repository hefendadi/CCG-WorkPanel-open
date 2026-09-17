const MDM_API = '/api/v1/mdm';
const UI_DENSITY_STORAGE_KEY = 'ccgtools.ui.density';
const UI_SIDEBAR_STORAGE_KEY = 'ccgtools.ui.sidebar.collapsed';

const MDM_RESOURCES = [
  { key: 'customers', label: '客户', singular: '客户' },
  { key: 'channels', label: '渠道', singular: '渠道' },
  { key: 'salesreps', label: '销售代表', singular: '销售代表' },
  { key: 'products', label: '商品', singular: '商品' },
  { key: 'skus', label: 'SKU', singular: 'SKU' },
  { key: 'regions', label: '大区', singular: '大区' },
  { key: 'provinces', label: '省份', singular: '省份' },
];

const CUSTOMER_FIELDS = [
  'customer_name', 'customer_code', 'organization', 'department', 'business_type',
  'market_type', 'format_type', 'channel_detail', 'is_direct', 'source_created_ym',
  'channel_stable_id', 'salesrep_stable_id', 'region_stable_id',
  'province_stable_id', 'parent_customer_stable_id',
];

const FIELD_LABELS = {
  customer_name: '客户名称', customer_code: '客户编码', organization: '组织',
  department: '部门', business_type: '业务类型（Business Type）', market_type: '市场类型（Market Type）',
  format_type: '业态', channel_detail: '渠道明细', is_direct: '是否直营',
  source_created_ym: '创建年月', channel_stable_id: '渠道',
  salesrep_stable_id: '销售代表', region_stable_id: '大区', province_stable_id: '省份',
  parent_customer_stable_id: '上级客户',
};

const state = {
  user: null,
  routeSequence: 0,
  references: null,
  customerPage: null,
  customerFilters: null,
  customer: null,
  editing: false,
  parentSearchTimer: null,
  dialogAction: null,
  toastTimer: null,
};

function routeFromPath(pathname) {
  const path = String(pathname || '').replace(/\/+$/, '') || '/';
  const goodsImportRoute = globalThis.MDMGoodsImport && globalThis.MDMGoodsImport.routeFromPath(path);
  if (goodsImportRoute) return goodsImportRoute;
  const skuImportRoute = globalThis.MDMSKUImport && globalThis.MDMSKUImport.routeFromPath(path);
  if (skuImportRoute) return skuImportRoute;
  const productImportRoute = globalThis.MDMProductImport && globalThis.MDMProductImport.routeFromPath(path);
  if (productImportRoute) return productImportRoute;
  const importRoute = globalThis.MDMCustomerImport && globalThis.MDMCustomerImport.routeFromPath(path);
  if (importRoute) return importRoute;
  if (path === '/mdm') return { name: 'overview', section: 'overview' };
  if (path === '/mdm/customers') return { name: 'customers', section: 'customers' };
  if (path === '/mdm/customers/new') return { name: 'customer-create', section: 'customers' };
  if (path === '/mdm/products') return { name: 'products', section: 'products' };
  if (path === '/mdm/products/new') return { name: 'product-create', section: 'products' };
  if (path === '/mdm/skus') return { name: 'skus', section: 'products' };
  const referenceMatch = path.match(/^\/mdm\/references\/(channels|salesreps|regions|provinces)(?:\/(new|[^/]+))?$/);
  if (referenceMatch) {
    const tail = referenceMatch[2];
    return {
      name: !tail ? 'reference-list' : tail === 'new' ? 'reference-create' : 'reference-detail',
      section: 'references', resource: referenceMatch[1], stableId: tail && tail !== 'new' ? decodeURIComponent(tail) : null,
    };
  }
  const match = path.match(/^\/mdm\/customers\/([^/]+)$/);
  if (match) return { name: 'customer-detail', section: 'customers', stableId: decodeURIComponent(match[1]) };
  const productMatch = path.match(/^\/mdm\/products\/([^/]+)$/);
  if (productMatch) return { name: 'product-detail', section: 'products', stableId: decodeURIComponent(productMatch[1]) };
  const skuMatch = path.match(/^\/mdm\/skus\/([^/]+)$/);
  if (skuMatch) return { name: 'sku-detail', section: 'products', stableId: decodeURIComponent(skuMatch[1]) };
  return { name: 'not-found', section: '' };
}

function canWrite(user = state.user) {
  return Boolean(user && hasPermission('mdm', 'EDIT', user));
}

function defaultCustomerFilters(search = '') {
  const params = new URLSearchParams(search);
  const pageSize = Number(params.get('page_size'));
  const page = Number(params.get('page'));
  const sort = ['updated_at', 'customer_name', 'customer_code', 'stable_id'].includes(params.get('sort'))
    ? params.get('sort') : 'updated_at';
  const order = params.get('order') === 'asc' ? 'asc' : 'desc';
  return {
    search: params.get('search') || '',
    status: params.get('status') === 'INACTIVE' ? 'INACTIVE' : 'ACTIVE',
    channel_stable_id: params.get('channel_stable_id') || '',
    salesrep_stable_id: params.get('salesrep_stable_id') || '',
    region_stable_id: params.get('region_stable_id') || '',
    province_stable_id: params.get('province_stable_id') || '',
    sort,
    order,
    page: Number.isInteger(page) && page > 0 ? page : 1,
    page_size: [25, 50, 100].includes(pageSize) ? pageSize : 50,
  };
}

function buildCustomerQuery(filters) {
  const params = new URLSearchParams();
  const keys = [
    'search', 'status', 'channel_stable_id', 'salesrep_stable_id',
    'region_stable_id', 'province_stable_id', 'sort', 'order', 'page', 'page_size',
  ];
  keys.forEach((key) => {
    const value = filters[key];
    if (value !== '' && value !== null && value !== undefined) params.set(key, String(value));
  });
  return params.toString();
}

function validationMessage(details) {
  const errors = details && Array.isArray(details.errors) ? details.errors : [];
  if (!errors.length) return '字段校验未通过，请检查必填项和输入格式。';
  const first = errors[0];
  const location = Array.isArray(first.loc) ? first.loc.filter((item) => !['body', 'query'].includes(item)).join('.') : '';
  return `${location ? `${FIELD_LABELS[location] || location}：` : ''}${first.msg || '输入不合法'}`;
}

function normalizeApiError(status, payload = {}) {
  const envelope = payload && payload.error ? payload.error : {};
  const code = envelope.code || (status === 401 ? 'AUTH_UNAUTHORIZED' : 'MDM_HTTP_ERROR');
  const details = envelope.details || {};
  const inUse = /_IN_USE$/.test(code);
  const messages = {
    401: '登录状态已失效，请重新登录。',
    403: '当前角色为只读用户，无权执行此操作。',
    404: '客户不存在或已不可见。',
    409: inUse
      ? `当前主档仍被其他有效关系引用，无法停用${details.active_reference_count ? `（有效引用 ${details.active_reference_count} 条）` : ''}。`
      : '当前操作与主档状态或关系规则冲突，请刷新后重试。',
    422: validationMessage(details),
    500: 'MDM 服务暂时不可用，请稍后重试。',
  };
  if (code === 'MDM_INACTIVE_REFERENCE') messages[409] = '所选关系已停用，不能用于新的主档关系。';
  if (code === 'MDM_DUPLICATE_CUSTOMER_CODE') messages[409] = '客户编码已被其他 Customer 使用，请使用不同的编码。';
  if (code === 'MDM_SELF_PARENT') messages[422] = '客户不能选择自己作为上级客户。';
  const message = messages[status] || envelope.message || '请求失败，请稍后重试。';
  const errorObject = new Error(message);
  errorObject.status = status;
  errorObject.code = code;
  errorObject.details = details;
  return errorObject;
}

async function mdmFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response;
  try {
    response = await authFetch(MDM_API + path, { ...options, headers });
  } catch (requestError) {
    if (requestError && requestError.message === 'unauthorized') throw normalizeApiError(401, {});
    throw requestError;
  }
  let payload = {};
  try { payload = await response.json(); } catch (_error) { payload = {}; }
  if (!response.ok) throw normalizeApiError(response.status, payload);
  return payload;
}

async function downloadMdmTemplate(path, filename) {
  const response = await authFetch(MDM_API + path);
  if (!response.ok) {
    let payload = {};
    try { payload = await response.json(); } catch (_error) { payload = {}; }
    throw normalizeApiError(response.status, payload);
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  try {
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }
}

function displayValue(value, fallback = '未提供') {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}

function formatTime(value) {
  if (!value) return '未提供';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date).replaceAll('/', '-');
}

function statusChip(status) {
  const active = status === 'ACTIVE';
  return `<span class="status-chip ${active ? 'status-active' : 'status-inactive'}">${statusLabel(status)}</span>`;
}

function statusLabel(status) {
  return status === 'ACTIVE' ? '有效' : status === 'INACTIVE' ? '停用' : displayValue(status);
}

function entityReference(reference, empty = '未设置', resource = '') {
  if (!reference) return `<span class="entity-ref"><strong>${esc(empty)}</strong></span>`;
  const lookup = resource ? refItem(resource, reference.stable_id) : null;
  const lookupName = lookup ? resourceName(resource, lookup).name : '';
  const name = reference.name || lookupName || '未命名';
  const stateSuffix = lookup ? referenceStateSuffix(resource, lookup, true) : '';
  return `<span class="entity-ref"><strong>${esc(name)}${esc(stateSuffix)}</strong><small>${esc(reference.stable_id)}</small></span>`;
}

function pageHeader(title, description, actions = '', _breadcrumb = '') {
  return `<header class="ccg-page-header">
      <div><h1>${esc(title)}</h1><p>${description}</p></div>
      <div class="ccg-page-actions">${actions}</div>
    </header>`;
}

function importDensityToggleMarkup() {
  const density = document.getElementById('app-shell')?.dataset.density === 'compact' ? 'compact' : 'comfortable';
  return `<div class="ccg-density-toggle" aria-label="表格密度"><button class="ccg-density-option ${density === 'comfortable' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="comfortable" aria-pressed="${density === 'comfortable'}">舒适</button><button class="ccg-density-option ${density === 'compact' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="compact" aria-pressed="${density === 'compact'}">紧凑</button></div>`;
}

function importCenterChrome(pathname) {
  const path = String(pathname || '');
  const types = [
    ['customer', 'Customer', '/mdm/import-center'],
    ['product', 'Product', '/mdm/product-import-center'],
    ['sku', 'SKU', '/mdm/sku-import-center'],
    ['goods', 'Goods', '/mdm/goods-import-center'],
  ];
  const active = path.startsWith('/mdm/product-import-center') ? 'product'
    : path.startsWith('/mdm/sku-import-center') ? 'sku'
      : path.startsWith('/mdm/goods-import-center') ? 'goods' : 'customer';
  const tabs = `<nav class="ccg-local-tabs ccg-import-types" aria-label="导入类型">${types.map(([key, label, href]) => `<a class="ccg-local-tab ${key === active ? 'active' : ''}" href="${href}" data-nav="${href}" ${key === active ? 'aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
  const stage = /\/upload$/.test(path) ? 'upload' : /\/review$/.test(path) ? 'review' : /\/commit$/.test(path) ? 'commit' : '';
  if (!stage) return tabs;
  const steps = [['upload', '1', '上传与校验'], ['review', '2', '结果审核'], ['commit', '3', '提交入库']];
  const stageIndex = steps.findIndex(([key]) => key === stage);
  const workflow = `<ol class="ccg-import-workflow" aria-label="导入流程">${steps.map(([key, number, label], index) => `<li class="${index < stageIndex ? 'complete' : key === stage ? 'active' : ''}" ${key === stage ? 'aria-current="step"' : ''}><span>${index < stageIndex ? '✓' : number}</span>${label}</li>`).join('')}</ol>`;
  return `${tabs}${workflow}`;
}

function importPageHeader(title, description, actions = '') {
  return `${pageHeader(title, description, actions)}${importCenterChrome(location.pathname)}`;
}

function importTableToolsMarkup() {
  return `<div class="ccg-import-table-tools"><span>批次数据</span>${importDensityToggleMarkup()}</div>`;
}

function importReviewToolsMarkup() {
  return `<div class="ccg-import-review-tools">${importDensityToggleMarkup()}</div>`;
}

function loadingMarkup(kind = 'cards') {
  const blocks = kind === 'table' ? 6 : 4;
  return `<section class="ccg-state ccg-state-loading" aria-label="正在加载"><div class="page-loading"><span class="skeleton skeleton-title"></span><span class="skeleton skeleton-line"></span><div class="skeleton-grid">${'<span></span>'.repeat(blocks)}</div></div></section>`;
}

function stateMarkup(type, title, copy, actionLabel = '', action = '') {
  const icon = type === 'error' ? '!' : type === 'access' ? '⌁' : '○';
  return `<section class="ccg-state ccg-state-${esc(type)}"><span class="ccg-state-icon" aria-hidden="true">${icon}</span><div class="ccg-state-copy"><h2>${esc(title)}</h2><p>${esc(copy)}</p></div>${actionLabel ? `<button class="ccg-button ccg-button-secondary" type="button" data-action="${esc(action)}">${esc(actionLabel)}</button>` : ''}</section>`;
}

function setTopbar(title, section) {
  if (globalThis.CCGShell) {
    globalThis.CCGShell.setPage(title, 'Master Data');
    return;
  }
  document.title = `${title} · CCGtools-open`;
  document.getElementById('topbar-title').textContent = title;
  document.querySelectorAll('.ccg-sidebar-nav a[data-section]').forEach((link) => {
    const referenceMatches = section !== 'references' || location.pathname.startsWith(link.dataset.nav || '');
    const active = link.dataset.section === section && referenceMatches;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
}

function setUiDensity(value, persist = true) {
  if (globalThis.CCGShell) return globalThis.CCGShell.setDensity(value, persist);
  const density = value === 'compact' ? 'compact' : 'comfortable';
  const shell = document.getElementById('app-shell');
  if (shell) {
    shell.dataset.density = density;
    shell.setAttribute('data-density', density);
  }
  document.querySelectorAll('[data-action="ui-density"]').forEach((button) => {
    const active = button.dataset.densityValue === density;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  if (persist) localStorage.setItem(UI_DENSITY_STORAGE_KEY, density);
  return density;
}

function setSidebarCollapsed(collapsed, persist = true) {
  if (globalThis.CCGShell) return globalThis.CCGShell.setSidebarCollapsed(collapsed, persist);
  const sidebar = document.getElementById('app-sidebar');
  const toggle = document.getElementById('sidebar-toggle');
  if (sidebar) sidebar.classList.toggle('is-collapsed', Boolean(collapsed));
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
  }
  if (persist) localStorage.setItem(UI_SIDEBAR_STORAGE_KEY, collapsed ? '1' : '0');
  return Boolean(collapsed);
}

function initUiPreferences() {
  if (globalThis.CCGShell) return globalThis.CCGShell.applyPreferences();
  setUiDensity(localStorage.getItem(UI_DENSITY_STORAGE_KEY), false);
  setSidebarCollapsed(localStorage.getItem(UI_SIDEBAR_STORAGE_KEY) === '1', false);
}

function setContent(html) {
  const content = document.getElementById('mdm-content');
  content.innerHTML = html;
  content.focus({ preventScroll: true });
}

function showToast(message) {
  const toast = document.getElementById('toast');
  clearTimeout(state.toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  state.toastTimer = setTimeout(() => { toast.hidden = true; }, 3200);
}

function navigate(path, replace = false) {
  if (replace) history.replaceState({}, '', path); else history.pushState({}, '', path);
  route();
}

function updateCustomerUrl(filters, replace = true) {
  const query = buildCustomerQuery(filters);
  const target = `/mdm/customers${query ? `?${query}` : ''}`;
  if (replace) history.replaceState({}, '', target); else history.pushState({}, '', target);
}

async function fetchCount(resource, status) {
  return mdmFetch(`/${resource}?page=1&page_size=1&status=${status}`);
}

async function renderOverview(sequence) {
  setTopbar('数据总览', 'overview');
  setContent(loadingMarkup());
  try {
    const countCalls = MDM_RESOURCES.flatMap((resource) => [
      fetchCount(resource.key, 'ACTIVE'), fetchCount(resource.key, 'INACTIVE'),
    ]);
    const healthPromise = authFetch('/api/health').then(async (response) => ({ ok: response.ok, body: await response.json() }));
    const [counts, health] = await Promise.all([Promise.all(countCalls), healthPromise]);
    if (sequence !== state.routeSequence) return;
    const cards = MDM_RESOURCES.map((resource, index) => {
      const active = counts[index * 2].total;
      const inactive = counts[index * 2 + 1].total;
      const clickable = true;
      const target = resource.key === 'customers' ? '/mdm/customers' : resource.key === 'products' ? '/mdm/products' : resource.key === 'skus' ? '/mdm/skus' : `/mdm/references/${resource.key}`;
      return `<article class="summary-card ${clickable ? 'clickable' : ''}" ${clickable ? `data-nav="${target}" role="link" tabindex="0"` : ''}>
        <div class="summary-label"><span><i class="entity-dot"></i>${esc(resource.label)}</span>${clickable ? '<span>→</span>' : ''}</div>
        <strong class="summary-total">${new Intl.NumberFormat('zh-CN').format(active + inactive)}</strong>
        <div class="summary-split"><span>有效 <b>${active}</b></span><span>停用 <b>${inactive}</b></span></div>
      </article>`;
    }).join('');
    const refreshedAt = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date());
    setContent(`${pageHeader('主数据总览', '七类核心主档的实时数量。有效与停用数量均来自现有分页接口，不使用静态示例数字。')}
      <section class="summary-grid" aria-label="主数据汇总">${cards}</section>
      <section class="overview-meta">
        <article class="panel"><div class="panel-head"><div><h2>系统状态</h2><p>当前连接与权限</p></div></div><div class="panel-body">
          <div class="meta-row"><span>MDM 服务</span><strong>${health.ok && health.body.status === 'ok' ? '正常' : '不可用'}</strong></div>
          <div class="meta-row"><span>当前权限</span><strong>${canWrite() ? 'MDM EDIT · 可管理' : 'MDM VIEW · 仅查看'}</strong></div>
          <div class="meta-row"><span>更新时间</span><strong>${esc(refreshedAt)}</strong></div>
        </div></article>
        <aside class="ccg-capability-note"><strong>当前可见范围</strong>数据质量、导入状态与近期活动目前没有可读取的汇总接口，因此本页不展示推测指标。客户主档工作区已可完整使用。</aside>
      </section>`);
  } catch (error) {
    if (sequence !== state.routeSequence) return;
    setContent(`${pageHeader('主数据总览', '七类核心主档的实时数量。')}${stateMarkup('error', '数据总览加载失败', error.message, '重试', 'retry-route')}`);
  }
}

async function fetchAll(resource, status) {
  const items = [];
  let page = 1;
  let pages = 1;
  do {
    const response = await mdmFetch(`/${resource}?page=${page}&page_size=200&status=${status}&sort=stable_id&order=asc`);
    items.push(...response.items);
    pages = response.pages;
    page += 1;
  } while (page <= pages);
  return items;
}

async function loadReferences() {
  if (state.references) return state.references;
  const resources = ['channels', 'salesreps', 'regions', 'provinces'];
  const results = await Promise.all(resources.flatMap((resource) => [fetchAll(resource, 'ACTIVE'), fetchAll(resource, 'INACTIVE')]));
  state.references = {};
  resources.forEach((resource, index) => {
    state.references[resource] = [...results[index * 2], ...results[index * 2 + 1]];
  });
  return state.references;
}

function resourceName(resource, item) {
  const fields = {
    channels: ['channel_name', 'channel_code'], salesreps: ['salesrep_name', 'employee_code'],
    regions: ['region_name', 'region_code'], provinces: ['province_name', 'province_code'],
  };
  const [nameField, codeField] = fields[resource];
  return { name: item[nameField] || '未命名', code: item[codeField] || '', stableId: item.stable_id };
}

function referenceStateSuffix(resource, item, currentRelation = false) {
  const historical = resource === 'channels' && item.channel_type === 'HISTORICAL';
  if (historical) return item.status === 'INACTIVE' ? '（历史口径 · 停用）' : '（历史口径）';
  if (item.status === 'INACTIVE') return currentRelation ? '（停用，仅保留当前关系）' : '（停用）';
  return '';
}

function filterOptions(resource, selected = '') {
  const items = state.references ? state.references[resource] || [] : [];
  return `<option value="">全部</option>${items.map((item) => {
    const label = resourceName(resource, item);
    return `<option value="${esc(item.stable_id)}" ${item.stable_id === selected ? 'selected' : ''}>${esc(label.name)}${referenceStateSuffix(resource, item)}</option>`;
  }).join('')}`;
}

function relationSelectOptions(resource, selected = '', predicate = () => true) {
  const items = state.references ? state.references[resource] || [] : [];
  const choices = items.filter((item) => (item.status === 'ACTIVE' && predicate(item)) || item.stable_id === selected);
  return `<option value="">请选择</option>${choices.map((item) => {
    const label = resourceName(resource, item);
    return `<option value="${esc(item.stable_id)}" ${item.stable_id === selected ? 'selected' : ''}>${esc(label.name)} · ${esc(item.stable_id)}${referenceStateSuffix(resource, item, true)}</option>`;
  }).join('')}`;
}

function customerFiltersMarkup(filters) {
  return `<form id="customer-filter-form" class="ccg-master-filter ccg-customer-filter">
    <label class="ccg-filter-field ccg-customer-search"><span class="ccg-filter-label">关键词</span><input class="ccg-input" name="search" value="${esc(filters.search)}" placeholder="客户名称 / 客户编码 / 系统唯一编号前缀"></label>
    <label class="ccg-filter-field"><span class="ccg-filter-label">状态</span><select class="ccg-select" name="status"><option value="ACTIVE" ${filters.status === 'ACTIVE' ? 'selected' : ''}>有效</option><option value="INACTIVE" ${filters.status === 'INACTIVE' ? 'selected' : ''}>停用</option></select></label>
    <label class="ccg-filter-field"><span class="ccg-filter-label">渠道 / Channel</span><select class="ccg-select" name="channel_stable_id">${filterOptions('channels', filters.channel_stable_id)}</select></label>
    <label class="ccg-filter-field"><span class="ccg-filter-label">销售代表 / SalesRep</span><select class="ccg-select" name="salesrep_stable_id">${filterOptions('salesreps', filters.salesrep_stable_id)}</select></label>
    <label class="ccg-filter-field"><span class="ccg-filter-label">大区</span><select class="ccg-select" name="region_stable_id">${filterOptions('regions', filters.region_stable_id)}</select></label>
    <label class="ccg-filter-field"><span class="ccg-filter-label">省份</span><select class="ccg-select" name="province_stable_id">${filterOptions('provinces', filters.province_stable_id)}</select></label>
    <label class="ccg-filter-field"><span class="ccg-filter-label">排序</span><select class="ccg-select" name="sort-order"><option value="updated_at:desc" ${filters.sort === 'updated_at' && filters.order === 'desc' ? 'selected' : ''}>最近更新</option><option value="customer_name:asc" ${filters.sort === 'customer_name' ? 'selected' : ''}>客户名称</option><option value="customer_code:asc" ${filters.sort === 'customer_code' ? 'selected' : ''}>客户编码</option><option value="stable_id:asc" ${filters.sort === 'stable_id' ? 'selected' : ''}>系统唯一编号</option></select></label>
    <div class="ccg-customer-filter-actions"><button class="ccg-button ccg-button-primary" type="submit">筛选</button><button class="ccg-button ccg-button-secondary" type="button" data-action="filters-reset">重置</button></div>
  </form>`;
}

function customerRowsMarkup(items) {
  return items.map((customer) => `<tr>
    <td class="ccg-customer-name-cell"><a class="ccg-customer-name" href="/mdm/customers/${encodeURIComponent(customer.stable_id)}" data-nav="/mdm/customers/${encodeURIComponent(customer.stable_id)}">${esc(customer.customer_name)}</a><span class="ccg-customer-stable ccg-mono">${esc(customer.stable_id)}<button class="copy-mini" type="button" title="复制系统唯一编号" aria-label="复制系统唯一编号 ${esc(customer.stable_id)}" data-action="copy" data-copy="${esc(customer.stable_id)}">□</button></span></td>
    <td class="ccg-mono">${esc(displayValue(customer.customer_code))}</td>
    <td>${entityReference(customer.channel, '未设置', 'channels')}</td>
    <td>${entityReference(customer.salesrep, '未设置', 'salesreps')}</td>
    <td>${entityReference(customer.region, '未设置', 'regions')}</td>
    <td>${entityReference(customer.province, '未设置', 'provinces')}</td>
    <td>${entityReference(customer.parent_customer, '无上级客户')}</td>
    <td class="ccg-customer-status"><span class="ccg-status ${customer.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(customer.status)}</span></td>
    <td class="ccg-tabular ccg-mono ccg-col-time">${esc(formatTime(customer.updated_at))}</td>
  </tr>`).join('');
}

function hasCustomerFilters(filters) {
  return Boolean(filters.search || filters.channel_stable_id || filters.salesrep_stable_id || filters.region_stable_id || filters.province_stable_id || filters.status !== 'ACTIVE');
}

function renderCustomerTable() {
  const page = state.customerPage;
  const filters = state.customerFilters;
  const density = document.getElementById('app-shell')?.dataset.density === 'compact' ? 'compact' : 'comfortable';
  const actions = canWrite() ? '<button class="ccg-button ccg-button-primary" type="button" data-action="customer-new">+ 新建客户</button>' : '';
  const filtered = hasCustomerFilters(filters);
  const rows = page.items.length ? customerRowsMarkup(page.items) : `<tr><td class="ccg-table-empty" colspan="9">${stateMarkup('empty', filtered ? '筛选条件下暂无客户' : '暂无客户', filtered ? '当前筛选条件没有匹配结果，可重置后查看全部有效客户。' : '当前还没有客户主档。', filtered ? '重置筛选' : '', filtered ? 'filters-reset' : '')}</td></tr>`;
  const from = page.total ? (page.page - 1) * page.page_size + 1 : 0;
  const to = Math.min(page.page * page.page_size, page.total);
  setContent(`<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>客户主档</h1><p>客户编码是唯一业务编码；系统唯一编号用于内部稳定识别客户。所有搜索与筛选均由服务端处理。</p></div><div class="ccg-page-actions">${actions}</div></header>
    ${!canWrite() ? '<div class="banner banner-info">当前为只读模式。你可以查看、搜索和筛选，但不能新建、编辑或改变状态。</div>' : ''}
    <section class="ccg-panel ccg-master-list ccg-customer-workspace">${customerFiltersMarkup(filters)}
      <div class="ccg-master-table-tools ccg-customer-table-tools"><span class="ccg-tabular">Customer 列表 · ${page.total} 条</span><div class="ccg-density-toggle" aria-label="表格密度"><button class="ccg-density-option ${density === 'comfortable' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="comfortable" aria-pressed="${density === 'comfortable'}">舒适</button><button class="ccg-density-option ${density === 'compact' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="compact" aria-pressed="${density === 'compact'}">紧凑</button></div></div>
      <div class="ccg-table-scroll" role="region" aria-label="Customer 列表，可横向滚动查看关系字段" tabindex="0"><table class="ccg-table ccg-customer-table"><thead><tr><th>客户名称</th><th>客户编码</th><th>渠道 / Channel</th><th>销售代表 / SalesRep</th><th>大区</th><th>省份</th><th>上级客户</th><th>状态</th><th>更新时间</th></tr></thead><tbody>${rows}</tbody></table></div>
      <footer class="ccg-pagination"><span>第 ${from}–${to} 条，共 ${page.total} 条</span><div class="ccg-pagination-controls"><label>每页 <select class="ccg-select" data-action="page-size"><option value="25" ${filters.page_size === 25 ? 'selected' : ''}>25</option><option value="50" ${filters.page_size === 50 ? 'selected' : ''}>50</option><option value="100" ${filters.page_size === 100 ? 'selected' : ''}>100</option></select></label><button class="ccg-button ccg-button-secondary" type="button" data-action="page-prev" ${page.page <= 1 ? 'disabled' : ''}>上一页</button><span class="ccg-tabular">第 ${page.page} / ${Math.max(page.pages, 1)} 页</span><button class="ccg-button ccg-button-secondary" type="button" data-action="page-next" ${page.page >= page.pages ? 'disabled' : ''}>下一页</button></div></footer>
    </section></div>`);
}

async function loadCustomerPage(sequence = state.routeSequence) {
  const query = buildCustomerQuery(state.customerFilters);
  try {
    const page = await mdmFetch(`/customers?${query}`);
    if (sequence !== state.routeSequence) return;
    state.customerPage = page;
    renderCustomerTable();
  } catch (error) {
    if (sequence !== state.routeSequence) return;
    const actions = canWrite() ? '<button class="ccg-button ccg-button-primary" type="button" data-action="customer-new">+ 新建客户</button>' : '';
    setContent(`<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>客户主档</h1><p>客户主档支持服务端搜索、筛选与分页。</p></div><div class="ccg-page-actions">${actions}</div></header>${stateMarkup('error', '客户列表加载失败', error.message, '重试', 'retry-route')}</div>`);
  }
}

async function renderCustomers(sequence) {
  setTopbar('客户主档', 'customers');
  setContent(loadingMarkup('table'));
  state.customerFilters = defaultCustomerFilters(location.search);
  try {
    await loadReferences();
    if (sequence !== state.routeSequence) return;
    await loadCustomerPage(sequence);
  } catch (error) {
    if (sequence !== state.routeSequence) return;
    setContent(`<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>客户主档</h1><p>客户主档支持服务端搜索、筛选与分页。</p></div></header>${stateMarkup('error', '客户主档工作区加载失败', error.message, '重试', 'retry-route')}</div>`);
  }
}

function detailValue(label, value, className = '') {
  return `<div${className ? ` class="${className}"` : ''}><dt>${esc(label)}</dt><dd>${esc(displayValue(value))}</dd></div>`;
}

function relationCard(label, reference, empty = '未设置', resource = '') {
  return `<div class="relation-card"><span>${esc(label)}</span>${entityReference(reference, empty, resource)}</div>`;
}

function customerDetailMarkup(customer) {
  const actionButton = customer.status === 'ACTIVE'
    ? '<button class="ccg-button ccg-button-danger-ghost" type="button" data-action="customer-lifecycle" data-lifecycle="deactivate">停用</button>'
    : '<button class="ccg-button ccg-button-success" type="button" data-action="customer-lifecycle" data-lifecycle="activate">启用</button>';
  const adminActions = canWrite() ? `<button class="ccg-button ccg-button-primary" type="button" data-action="customer-edit">编辑</button>${actionButton}` : '';
  const status = `<span class="ccg-status ${customer.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(customer.status)}</span>`;
  return `<div class="ccg-customer-page ccg-customer-detail">
    ${!canWrite() ? '<div class="ccg-readonly-banner">当前为只读模式：你可以查看详情，但不能编辑或改变状态。</div>' : ''}
    <header class="ccg-page-header ccg-customer-detail-header"><div><div class="ccg-customer-title-row"><h1>${esc(customer.customer_name)}</h1>${status}</div><p><span class="ccg-mono">${esc(customer.customer_code)}</span><span class="ccg-customer-separator">·</span><span class="ccg-mono">${esc(customer.stable_id)}</span><button class="copy-mini" type="button" data-action="copy" data-copy="${esc(customer.stable_id)}" aria-label="复制系统唯一编号">□</button><span class="ccg-customer-separator">·</span><span class="ccg-tabular">更新于 ${esc(formatTime(customer.updated_at))}</span></p></div><div class="ccg-page-actions">${adminActions}</div></header>
    <section class="ccg-panel ccg-customer-detail-panel">
      <div class="ccg-customer-detail-grid">
        <section class="ccg-customer-detail-section"><h2>负责关系</h2><div class="ccg-customer-owner-grid">${relationCard('渠道 / Channel', customer.channel, '未设置', 'channels')}${relationCard('销售代表 / SalesRep', customer.salesrep, '未设置', 'salesreps')}</div></section>
        <section class="ccg-customer-detail-section"><h2>基础信息</h2><dl class="ccg-definition-grid">${detailValue('客户名称', customer.customer_name)}${detailValue('客户编码', customer.customer_code, 'ccg-mono')}${detailValue('系统唯一编号', customer.stable_id, 'ccg-mono')}${detailValue('状态', statusLabel(customer.status))}</dl></section>
        <section class="ccg-customer-detail-section"><h2>业务分类</h2><dl class="ccg-definition-grid">${detailValue('组织', customer.organization)}${detailValue('部门', customer.department)}${detailValue('业务类型（Business Type）', customer.business_type)}${detailValue('市场类型（Market Type）', customer.market_type)}${detailValue('业态', customer.format_type)}${detailValue('渠道明细', customer.channel_detail)}${detailValue('是否直营', customer.is_direct)}${detailValue('创建年月', customer.source_created_ym)}</dl></section>
        <section class="ccg-customer-detail-section"><h2>区域信息与客户层级</h2><div class="ccg-customer-relation-grid">${relationCard('大区', customer.region, '未设置', 'regions')}${relationCard('省份', customer.province, '未设置', 'provinces')}${relationCard('上级客户', customer.parent_customer, '无上级客户')}</div><div class="ccg-capability-note"><strong>下级客户</strong>当前客户接口不支持反向查询，暂时无法准确展示下级客户及数量。</div></section>
        <section class="ccg-customer-detail-section"><h2>系统信息</h2><dl class="ccg-definition-grid">${detailValue('创建时间', formatTime(customer.created_at), 'ccg-tabular')}${detailValue('更新时间', formatTime(customer.updated_at), 'ccg-tabular')}</dl></section>
      </div>
      <aside class="ccg-customer-capability"><strong>当前能力限制</strong><span>外部映射、别名与变更记录目前没有读取接口；此处明确标记为暂不支持，不将能力缺口显示成空数据。</span></aside>
    </section></div>`;
}

async function loadCustomerDetail(stableId, sequence = state.routeSequence, successMessage = '') {
  setTopbar('客户详情', 'customers');
  setContent(loadingMarkup());
  try {
    const [customer] = await Promise.all([mdmFetch(`/customers/${encodeURIComponent(stableId)}`), loadReferences()]);
    if (sequence !== state.routeSequence) return;
    state.customer = customer;
    state.editing = false;
    setContent(customerDetailMarkup(customer));
    if (successMessage) showToast(successMessage);
  } catch (error) {
    if (sequence !== state.routeSequence) return;
    const title = error.status === 404 ? '客户不存在' : '客户详情加载失败';
    const copy = error.status === 404 ? `${error.message} 系统唯一编号：${stableId}` : error.message;
    setContent(`<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>${esc(title)}</h1><p>${esc(copy)}</p></div><div class="ccg-page-actions"><button class="ccg-button ccg-button-secondary" type="button" data-action="back-customers">返回客户主档</button></div></header>${stateMarkup('error', title, copy, error.status === 404 ? '' : '重试', error.status === 404 ? '' : 'retry-route')}</div>`);
  }
}

function currentCustomerValues(customer) {
  return {
    customer_name: customer.customer_name || '', customer_code: customer.customer_code || '',
    organization: customer.organization || '', department: customer.department || '',
    business_type: customer.business_type || '', market_type: customer.market_type || '',
    format_type: customer.format_type || '', channel_detail: customer.channel_detail || '',
    is_direct: customer.is_direct === null || customer.is_direct === undefined ? null : Boolean(customer.is_direct),
    source_created_ym: customer.source_created_ym || '',
    channel_stable_id: customer.channel ? customer.channel.stable_id : '',
    salesrep_stable_id: customer.salesrep ? customer.salesrep.stable_id : '',
    region_stable_id: customer.region ? customer.region.stable_id : '',
    province_stable_id: customer.province ? customer.province.stable_id : '',
    parent_customer_stable_id: customer.parent_customer ? customer.parent_customer.stable_id : '',
  };
}

function comparable(value) {
  if (value === '' || value === undefined) return null;
  return value;
}

function diffCustomerPayload(current, next) {
  const changes = {};
  CUSTOMER_FIELDS.forEach((field) => {
    if (comparable(current[field]) !== comparable(next[field])) changes[field] = comparable(next[field]);
  });
  return changes;
}

function refItem(resource, stableId) {
  return ((state.references && state.references[resource]) || []).find((item) => item.stable_id === stableId) || null;
}

function relationDisplay(resource, stableId) {
  if (!stableId) return '未设置';
  if (resource === 'customers') {
    if (state.customer && state.customer.parent_customer && state.customer.parent_customer.stable_id === stableId) return `${state.customer.parent_customer.name} · ${stableId}`;
    const input = typeof document !== 'undefined' ? document.getElementById('parent-search') : null;
    return `${input && input.dataset.selectedName ? input.dataset.selectedName : '客户'} · ${stableId}`;
  }
  const item = refItem(resource, stableId);
  const value = item ? resourceName(resource, item) : null;
  return value ? `${value.name} · ${stableId}` : stableId;
}

function changeDisplay(field, value) {
  if (field === 'is_direct') return value === null ? '未设置' : value ? '是' : '否';
  const map = { channel_stable_id: 'channels', salesrep_stable_id: 'salesreps', region_stable_id: 'regions', province_stable_id: 'provinces', parent_customer_stable_id: 'customers' };
  if (map[field]) return relationDisplay(map[field], value);
  return displayValue(value, '未设置');
}

function provinceOptions(regionStableId, selected = '') {
  const all = state.references ? state.references.provinces || [] : [];
  const allowed = all.filter((province) => {
    if (province.stable_id === selected) return true;
    return province.status === 'ACTIVE';
  });
  return `<option value="">无需填写 / 未设置</option>${allowed.map((province) => `<option value="${esc(province.stable_id)}" ${province.stable_id === selected ? 'selected' : ''}>${esc(province.province_name)} · ${esc(province.stable_id)}${referenceStateSuffix('provinces', province, province.stable_id === selected)}</option>`).join('')}`;
}


function customerFormMarkup(customer = null) {
  const editing = Boolean(customer);
  const values = editing ? currentCustomerValues(customer) : {
    customer_name: '', customer_code: '', organization: '', department: '', business_type: '', market_type: '',
    format_type: '', channel_detail: '', is_direct: null, source_created_ym: '', channel_stable_id: '',
    salesrep_stable_id: '', region_stable_id: '', province_stable_id: '', parent_customer_stable_id: '',
  };
  const parentName = customer && customer.parent_customer ? customer.parent_customer.name : '';
  const channelPredicate = (item) => item.channel_type !== 'HISTORICAL';
  const formTitle = editing ? `编辑客户：${customer.customer_name}` : '新建客户';
  const formCopy = editing ? '只提交发生变化的字段。关系变更以系统唯一编号为准，并在右侧显示“当前 → 修改后”。' : '系统唯一编号由 MDM 服务创建；客户编码全局唯一，不允许重复。';
  const summary = editing ? '<aside class="ccg-panel ccg-master-form-summary ccg-customer-summary"><div class="ccg-panel-header"><div><h2>变更摘要</h2><p>修改主档关系只影响当前 MDM 归属，不自动重写历史交易数据。</p></div></div><div id="change-list" class="change-list"><span class="ccg-form-help">暂无变更</span></div></aside>' : '<aside class="ccg-panel ccg-master-form-summary ccg-customer-summary"><div class="ccg-panel-header"><div><h2>新建规则</h2><p>关系字段只提交系统唯一编号。渠道、销售代表、大区、省份与上级客户的名称仅用于识别。</p></div></div><div class="ccg-capability-note"><strong>基础信息</strong>系统唯一编号将由 MDM 服务自动生成；不会提交数据库内部编号。</div></aside>';
  return `<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>${esc(formTitle)}</h1><p>${esc(formCopy)}</p></div></header>
    <form id="customer-form" class="ccg-master-form ccg-customer-form" data-mode="${editing ? 'edit' : 'create'}">
      <div class="ccg-customer-form-stack">
        <section class="ccg-panel ccg-master-form-section ccg-customer-form-section"><div class="ccg-panel-header"><h2>基础信息</h2></div><div class="ccg-form-grid">
          <label class="ccg-form-field"><span class="ccg-form-label required">客户名称</span><input name="customer_name" value="${esc(values.customer_name)}" maxlength="255" required autocomplete="off"></label>
          <label class="ccg-form-field"><span class="ccg-form-label required">客户编码</span><input class="ccg-mono" name="customer_code" value="${esc(values.customer_code)}" maxlength="128" required autocomplete="off"><small class="ccg-form-help">业务编码全局唯一；停用客户仍占用该编码。</small></label>
          ${editing ? `<div class="ccg-form-field span-2"><span class="ccg-form-label">系统唯一编号</span><strong>${esc(customer.stable_id)}</strong><small class="ccg-form-help">由 MDM 服务管理，不可编辑。</small></div>` : ''}
        </div></section>
        <section class="ccg-panel ccg-master-form-section ccg-customer-form-section"><div class="ccg-panel-header"><h2>业务分类</h2></div><div class="ccg-form-grid">
          <label class="ccg-form-field"><span class="ccg-form-label">组织</span><input name="organization" value="${esc(values.organization)}" maxlength="128"></label>
          <label class="ccg-form-field"><span class="ccg-form-label">部门</span><input name="department" value="${esc(values.department)}" maxlength="128"></label>
          <label class="ccg-form-field"><span class="ccg-form-label">业务类型（Business Type）</span><input name="business_type" value="${esc(values.business_type)}" maxlength="32"></label>
          <label class="ccg-form-field"><span class="ccg-form-label">市场类型（Market Type）</span><input name="market_type" value="${esc(values.market_type)}" maxlength="32"></label>
          <label class="ccg-form-field"><span class="ccg-form-label">业态</span><input name="format_type" value="${esc(values.format_type)}" maxlength="64"></label>
          <label class="ccg-form-field"><span class="ccg-form-label">是否直营</span><select name="is_direct"><option value="" ${values.is_direct === null ? 'selected' : ''}>未设置</option><option value="true" ${values.is_direct === true ? 'selected' : ''}>是</option><option value="false" ${values.is_direct === false ? 'selected' : ''}>否</option></select></label>
          <label class="ccg-form-field span-2"><span class="ccg-form-label">渠道明细</span><input name="channel_detail" value="${esc(values.channel_detail)}" maxlength="255"></label>
          <label class="ccg-form-field"><span class="ccg-form-label">创建年月</span><input class="ccg-mono ccg-tabular" name="source_created_ym" value="${esc(values.source_created_ym)}" maxlength="4" placeholder="例如 0001"></label>
        </div></section>
        <section class="ccg-panel ccg-master-form-section ccg-customer-form-section"><div class="ccg-panel-header"><h2>负责关系</h2></div><div class="ccg-form-grid">
          <label class="ccg-form-field"><span class="ccg-form-label required">渠道</span><select name="channel_stable_id" required>${relationSelectOptions('channels', values.channel_stable_id, channelPredicate)}</select><small class="ccg-form-help">历史口径或已停用的渠道不可用于新关系。</small></label>
          <label class="ccg-form-field"><span class="ccg-form-label required">销售代表</span><select name="salesrep_stable_id" required>${relationSelectOptions('salesreps', values.salesrep_stable_id)}</select></label>
        </div></section>
        <section class="ccg-panel ccg-master-form-section ccg-customer-form-section"><div class="ccg-panel-header"><h2>区域与层级</h2></div><div class="ccg-form-grid">
          <label class="ccg-form-field"><span class="ccg-form-label">大区</span><select id="customer-region" name="region_stable_id">${relationSelectOptions('regions', values.region_stable_id)}</select><small class="ccg-form-help">省份可选，已设置时必须属于所选大区。</small></label>
          <label class="ccg-form-field"><span class="ccg-form-label">省份</span><select id="customer-province" name="province_stable_id" >${provinceOptions(values.region_stable_id, values.province_stable_id)}</select></label>
          <div class="ccg-form-field span-2 relation-picker"><span class="ccg-form-label">上级客户</span><input id="parent-search" value="${esc(parentName)}" data-selected-name="${esc(parentName)}" placeholder="输入客户名称 / 客户编码 / 系统唯一编号前缀搜索" autocomplete="off"><input id="parent-stable-id" name="parent_customer_stable_id" type="hidden" value="${esc(values.parent_customer_stable_id)}"><small class="ccg-form-help">可选。只会提交选中客户的系统唯一编号。</small><div id="parent-results" class="relation-results" hidden></div></div>
        </div></section>
        <div id="form-error" class="inline-error" hidden></div>
        <div class="ccg-form-actions"><button class="ccg-button ccg-button-secondary" type="button" data-action="customer-cancel-edit">取消</button><button id="customer-submit" class="ccg-button ccg-button-primary" type="submit">${editing ? '保存' : '新建客户'}</button></div>
      </div>
      ${summary}
    </form></div>`;
}

function collectCustomerForm(form) {
  const data = new FormData(form);
  const values = {};
  CUSTOMER_FIELDS.forEach((field) => {
    if (field === 'is_direct') {
      const raw = data.get(field);
      values[field] = raw === '' ? null : raw === 'true';
    } else {
      const raw = data.get(field);
      values[field] = raw === null ? '' : String(raw).trim();
    }
  });
  return values;
}

function validateCustomerForm(values, currentStableId = '') {
  if (!values.customer_name) return '客户名称不能为空。';
  if (!values.customer_code) return '客户编码不能为空。';
  if (!values.channel_stable_id) return '请选择渠道。';
  if (!values.salesrep_stable_id) return '请选择销售代表。';
  if (values.parent_customer_stable_id && values.parent_customer_stable_id === currentStableId) return '客户不能选择自己作为上级客户。';
  if (values.region_stable_id && values.province_stable_id) {
    const province = refItem('provinces', values.province_stable_id);
    if (province && province.region && province.region.stable_id !== values.region_stable_id) return '省份不属于所选大区。';
  }
  return '';
}

function updateChangeSummary() {
  if (!state.editing || !state.customer) return;
  const form = document.getElementById('customer-form');
  if (!form) return;
  const current = currentCustomerValues(state.customer);
  const next = collectCustomerForm(form);
  const changes = diffCustomerPayload(current, next);
  const list = document.getElementById('change-list');
  const fields = Object.keys(changes);
  list.innerHTML = fields.length ? fields.map((field) => `<div class="change-row"><span>${esc(FIELD_LABELS[field] || field)}</span><div class="change-values"><strong>当前：${esc(changeDisplay(field, current[field]))}</strong><i>→</i><strong>修改后：${esc(changeDisplay(field, changes[field]))}</strong></div></div>`).join('') : '<span class="ccg-form-help">暂无变更</span>';
  document.getElementById('customer-submit').disabled = fields.length === 0;
}

async function renderCustomerForm(customer = null) {
  if (!canWrite()) {
    setTopbar('客户主档 · 只读', 'customers');
    setContent(`<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>只读权限</h1><p>操作员在 MDM 中仅拥有查看权限。</p></div></header>${stateMarkup('access', '无法打开写入页面', '当前角色只能查看客户；MDM 服务也会拒绝任何写请求。', '返回客户主档', 'back-customers')}</div>`);
    return;
  }
  setTopbar(customer ? '编辑客户' : '新建客户', 'customers');
  setContent(loadingMarkup());
  try {
    await loadReferences();
    state.editing = Boolean(customer);
    setContent(customerFormMarkup(customer));
    if (customer) updateChangeSummary();
  } catch (error) {
    const title = customer ? '编辑客户' : '新建客户';
    setContent(`<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>${title}</h1><p>客户主档受控表单。</p></div></header>${stateMarkup('error', '表单加载失败', error.message, '重试', 'retry-route')}</div>`);
  }
}

function renderCreateSuccess(customer) {
  const warningMarkup = '';
  setTopbar('客户已创建', 'customers');
  const status = `<span class="ccg-status ${customer.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(customer.status)}</span>`;
  setContent(`<div class="ccg-customer-page"><header class="ccg-page-header"><div><h1>客户已创建</h1><p>MDM 服务已生成不可变的系统唯一编号。</p></div></header>${warningMarkup}<section class="ccg-panel ccg-customer-created"><div><div class="ccg-customer-title-row"><h2>${esc(customer.customer_name)}</h2>${status}</div><p><span class="ccg-mono">${esc(customer.customer_code)}</span><span class="ccg-customer-separator">·</span><span class="ccg-mono">${esc(customer.stable_id)}</span><button class="copy-mini" type="button" data-action="copy" data-copy="${esc(customer.stable_id)}" aria-label="复制系统唯一编号">□</button></p></div><div class="ccg-page-actions"><button class="ccg-button ccg-button-secondary" type="button" data-action="create-another">继续新建</button><a class="ccg-button ccg-button-primary" href="/mdm/customers/${encodeURIComponent(customer.stable_id)}" data-nav="/mdm/customers/${encodeURIComponent(customer.stable_id)}">查看详情</a></div></section></div>`);
}

async function submitCustomerForm(form) {
  const values = collectCustomerForm(form);
  const editing = form.dataset.mode === 'edit';
  const errorBox = document.getElementById('form-error');
  const validation = validateCustomerForm(values, editing && state.customer ? state.customer.stable_id : '');
  if (validation) {
    errorBox.textContent = validation;
    errorBox.hidden = false;
    return;
  }
  let payload = values;
  if (editing) payload = diffCustomerPayload(currentCustomerValues(state.customer), values);
  if (editing && Object.keys(payload).length === 0) return;
  const submit = document.getElementById('customer-submit');
  submit.disabled = true;
  submit.textContent = editing ? '保存中…' : '创建中…';
  errorBox.hidden = true;
  try {
    const response = await mdmFetch(editing ? `/customers/${encodeURIComponent(state.customer.stable_id)}` : '/customers', {
      method: editing ? 'PATCH' : 'POST', body: JSON.stringify(payload),
    });
    if (editing) {
      state.editing = false;
      await loadCustomerDetail(state.customer.stable_id, state.routeSequence, '客户已保存。');
    } else {
      renderCreateSuccess(response);
    }
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    submit.disabled = false;
    submit.textContent = editing ? '保存' : '新建客户';
  }
}

async function searchParents(keyword) {
  const results = document.getElementById('parent-results');
  if (!results) return;
  try {
    const query = new URLSearchParams({ page: '1', page_size: '20', status: 'ACTIVE', sort: 'customer_name', order: 'asc' });
    if (keyword) query.set('search', keyword);
    const response = await mdmFetch(`/customers?${query.toString()}`);
    const choices = response.items.filter((customer) => !state.customer || customer.stable_id !== state.customer.stable_id);
    results.innerHTML = choices.length ? choices.map((customer) => `<button class="relation-option" type="button" data-action="parent-option" data-stable-id="${esc(customer.stable_id)}" data-name="${esc(customer.customer_name)}"><span>${esc(customer.customer_name)}<small>${esc(customer.customer_code)} · 全局唯一</small></span><small title="系统唯一编号">${esc(customer.stable_id)}</small></button>`).join('') : '<div class="ccg-form-help" style="padding:10px">没有匹配的有效客户</div>';
    results.hidden = false;
  } catch (error) {
    results.innerHTML = `<div class="ccg-form-help" style="padding:10px">${esc(error.message)}</div>`;
    results.hidden = false;
  }
}

function syncProvinceOptions() {
  const region = document.getElementById('customer-region');
  const province = document.getElementById('customer-province');
  if (!region || !province) return;
  const current = province.value;
  province.innerHTML = provinceOptions(region.value, current);
  province.disabled = false;
}

function openLifecycleDialog(action) {
  if (!state.customer || !canWrite()) return;
  const deactivating = action === 'deactivate';
  const layer = document.getElementById('confirm-layer');
  const confirm = document.getElementById('dialog-confirm');
  document.getElementById('dialog-title').textContent = deactivating ? `停用 ${state.customer.customer_name}？` : `启用 ${state.customer.customer_name}？`;
  document.getElementById('dialog-copy').textContent = deactivating
    ? `客户：${state.customer.customer_name}\n系统唯一编号：${state.customer.stable_id}\n\n停用后不可用于新增业务。提交时由 MDM 服务最终校验引用关系，不会级联修改历史交易数据。`
    : `客户：${state.customer.customer_name}\n系统唯一编号：${state.customer.stable_id}\n\n启用后，该客户可重新用于新增业务关系。`;
  document.getElementById('dialog-icon').textContent = deactivating ? '!' : '✓';
  document.getElementById('dialog-error').hidden = true;
  confirm.textContent = deactivating ? '确认停用' : '确认启用';
  confirm.className = `ccg-button ${deactivating ? 'ccg-button-danger' : 'ccg-button-success'}`;
  confirm.disabled = false;
  state.dialogAction = async () => {
    confirm.disabled = true;
    confirm.textContent = deactivating ? '停用中…' : '启用中…';
    try {
      await mdmFetch(`/customers/${encodeURIComponent(state.customer.stable_id)}/${action}`, { method: 'POST' });
      closeDialog();
      await loadCustomerDetail(state.customer.stable_id, state.routeSequence, deactivating ? '客户已停用。' : '客户已启用。');
    } catch (error) {
      const box = document.getElementById('dialog-error');
      box.textContent = error.message;
      box.hidden = false;
      confirm.disabled = false;
      confirm.textContent = deactivating ? '确认停用' : '确认启用';
    }
  };
  layer.hidden = false;
  confirm.focus();
}

function closeDialog() {
  document.getElementById('confirm-layer').hidden = true;
  state.dialogAction = null;
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    showToast(`已复制 ${value}`);
  } catch (_error) {
    showToast(`系统唯一编号：${value}`);
  }
}

function applyCustomerFilters(form) {
  const data = new FormData(form);
  const [sort, order] = String(data.get('sort-order') || 'updated_at:desc').split(':');
  state.customerFilters = {
    ...state.customerFilters,
    search: String(data.get('search') || '').trim(), status: String(data.get('status') || 'ACTIVE'),
    channel_stable_id: String(data.get('channel_stable_id') || ''), salesrep_stable_id: String(data.get('salesrep_stable_id') || ''),
    region_stable_id: String(data.get('region_stable_id') || ''), province_stable_id: String(data.get('province_stable_id') || ''),
    sort, order, page: 1,
  };
  updateCustomerUrl(state.customerFilters, false);
  setContent(loadingMarkup('table'));
  loadCustomerPage();
}

function changePage(page) {
  if (!state.customerPage || page < 1 || page > state.customerPage.pages) return;
  state.customerFilters.page = page;
  updateCustomerUrl(state.customerFilters);
  setContent(loadingMarkup('table'));
  loadCustomerPage();
}

function bindEvents() {
  document.addEventListener('click', (event) => {
    const nav = event.target.closest('[data-nav]');
    if (nav) {
      event.preventDefault();
      navigate(nav.dataset.nav || nav.getAttribute('href'));
      return;
    }
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    if (globalThis.MDMGoodsImport && globalThis.MDMGoodsImport.handleAction(action, target)) return;
    if (globalThis.MDMSKUImport && globalThis.MDMSKUImport.handleAction(action, target)) return;
    if (globalThis.MDMProductImport && globalThis.MDMProductImport.handleAction(action, target)) return;
    if (globalThis.MDMCustomerImport && globalThis.MDMCustomerImport.handleAction(action, target)) return;
    if (globalThis.MDMReferenceMasters && globalThis.MDMReferenceMasters.handleAction(action, target)) return;
    if (globalThis.MDMProductSKU && globalThis.MDMProductSKU.handleAction(action, target)) return;
    if (action === 'sidebar-toggle' && !globalThis.CCGShell) {
      const sidebar = document.getElementById('app-sidebar');
      setSidebarCollapsed(!sidebar?.classList.contains('is-collapsed'));
    }
    else if (action === 'ui-density' && !globalThis.CCGShell) setUiDensity(target.dataset.densityValue);
    else if (action === 'logout' && !globalThis.CCGShell) logout();
    else if (action === 'retry-route') route();
    else if (action === 'back-overview') navigate('/mdm');
    else if (action === 'customer-new' || action === 'create-another') navigate('/mdm/customers/new');
    else if (action === 'back-customers') navigate('/mdm/customers');
    else if (action === 'filters-reset') navigate('/mdm/customers');
    else if (action === 'page-prev') changePage(state.customerPage.page - 1);
    else if (action === 'page-next') changePage(state.customerPage.page + 1);
    else if (action === 'copy') copyText(target.dataset.copy || '');
    else if (action === 'customer-edit') renderCustomerForm(state.customer);
    else if (action === 'customer-cancel-edit') {
      if (state.editing && state.customer) {
        state.editing = false;
        setTopbar('客户详情', 'customers');
        setContent(customerDetailMarkup(state.customer));
      } else navigate('/mdm/customers');
    }
    else if (action === 'customer-lifecycle') openLifecycleDialog(target.dataset.lifecycle);
    else if (action === 'dialog-cancel') closeDialog();
    else if (action === 'dialog-confirm' && state.dialogAction) state.dialogAction();
    else if (action === 'parent-option') {
      const input = document.getElementById('parent-search');
      input.value = target.dataset.name;
      input.dataset.selectedName = target.dataset.name;
      document.getElementById('parent-stable-id').value = target.dataset.stableId;
      document.getElementById('parent-results').hidden = true;
      updateChangeSummary();
    }
  });

  document.addEventListener('submit', (event) => {
    if (globalThis.MDMGoodsImport && globalThis.MDMGoodsImport.handleSubmit(event)) return;
    if (globalThis.MDMSKUImport && globalThis.MDMSKUImport.handleSubmit(event)) return;
    if (globalThis.MDMProductImport && globalThis.MDMProductImport.handleSubmit(event)) return;
    if (globalThis.MDMCustomerImport && globalThis.MDMCustomerImport.handleSubmit(event)) return;
    if (globalThis.MDMReferenceMasters && globalThis.MDMReferenceMasters.handleSubmit(event)) return;
    if (globalThis.MDMProductSKU && globalThis.MDMProductSKU.handleSubmit(event)) return;
    if (event.target.id === 'customer-filter-form') { event.preventDefault(); applyCustomerFilters(event.target); }
    if (event.target.id === 'customer-form') { event.preventDefault(); submitCustomerForm(event.target); }
  });

  document.addEventListener('change', (event) => {
    if (globalThis.MDMGoodsImport && globalThis.MDMGoodsImport.handleChange(event)) return;
    if (globalThis.MDMSKUImport && globalThis.MDMSKUImport.handleChange(event)) return;
    if (globalThis.MDMProductImport && globalThis.MDMProductImport.handleChange(event)) return;
    if (globalThis.MDMCustomerImport && globalThis.MDMCustomerImport.handleChange(event)) return;
    if (globalThis.MDMReferenceMasters && globalThis.MDMReferenceMasters.handleChange(event)) return;
    if (globalThis.MDMProductSKU && globalThis.MDMProductSKU.handleChange(event)) return;
    if (event.target.dataset.action === 'page-size') {
      state.customerFilters.page_size = Number(event.target.value);
      state.customerFilters.page = 1;
      updateCustomerUrl(state.customerFilters);
      setContent(loadingMarkup('table'));
      loadCustomerPage();
      return;
    }
    if (event.target.id === 'customer-region') syncProvinceOptions();
    if (event.target.form && event.target.form.id === 'customer-form') updateChangeSummary();
  });

  document.addEventListener('input', (event) => {
    if (globalThis.MDMReferenceMasters && globalThis.MDMReferenceMasters.handleInput(event)) return;
    if (globalThis.MDMProductSKU && globalThis.MDMProductSKU.handleInput(event)) return;
    if (event.target.id === 'parent-search') {
      if (event.target.value !== event.target.dataset.selectedName) document.getElementById('parent-stable-id').value = '';
      clearTimeout(state.parentSearchTimer);
      state.parentSearchTimer = setTimeout(() => searchParents(event.target.value.trim()), 180);
    }
    if (event.target.form && event.target.form.id === 'customer-form') updateChangeSummary();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !document.getElementById('confirm-layer').hidden) closeDialog();
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.summary-card[data-nav]')) {
      event.preventDefault();
      navigate(event.target.dataset.nav);
    }
  });

  window.addEventListener('popstate', route);
}

async function route() {
  const sequence = ++state.routeSequence;
  const current = routeFromPath(location.pathname);
  if (current.name === 'overview') await renderOverview(sequence);
  else if (current.section === 'goods-import-center') await MDMGoodsImport.render(current, sequence);
  else if (current.section === 'sku-import-center') await MDMSKUImport.render(current, sequence);
  else if (current.section === 'product-import-center') await MDMProductImport.render(current, sequence);
  else if (current.section === 'import-center') await MDMCustomerImport.render(current, sequence);
  else if (current.name === 'customers') await renderCustomers(sequence);
  else if (current.name === 'customer-create') await renderCustomerForm();
  else if (current.name === 'customer-detail') await loadCustomerDetail(current.stableId, sequence);
  else if (current.name === 'products') await MDMProductSKU.renderProducts(sequence);
  else if (current.name === 'product-create') await MDMProductSKU.renderProductForm();
  else if (current.name === 'product-detail') await MDMProductSKU.loadProductDetail(current.stableId, sequence);
  else if (current.name === 'skus') await MDMProductSKU.renderSkus(sequence);
  else if (current.name === 'sku-detail') await MDMProductSKU.loadSkuDetail(current.stableId, sequence);
  else if (current.name === 'reference-list') await MDMReferenceMasters.renderList(current.resource, sequence);
  else if (current.name === 'reference-create') await MDMReferenceMasters.renderForm(current.resource);
  else if (current.name === 'reference-detail') await MDMReferenceMasters.loadDetail(current.resource, current.stableId, sequence);
  else {
    setTopbar('页面不存在', '');
    setContent(stateMarkup('error', '页面不存在', '该 MDM 路由未在 public edition 中实现。', '返回数据总览', 'back-overview'));
  }
}

async function initMdm() {
  const user = await requireLogin();
  if (!user) return;
  if (!hasPermission('mdm', 'VIEW', user)) { location.href = '/'; return; }
  state.user = user;
  initUiPreferences();
  if (globalThis.CCGShell) globalThis.CCGShell.updateUser(user);
  syncUserManagementVisibility(document, user);
  document.getElementById('user-name').textContent = user.display_name || user.username;
  document.getElementById('user-role').textContent = canWrite(user) ? 'MDM EDIT · 可管理' : 'MDM VIEW · 仅查看';
  document.getElementById('user-avatar').textContent = String(user.display_name || user.username || '?').slice(0, 1).toUpperCase();
  document.getElementById('readonly-indicator').hidden = canWrite(user);
  bindEvents();
  await route();
}

globalThis.MDMTest = {
  routeFromPath, canWrite, defaultCustomerFilters, buildCustomerQuery, normalizeApiError,
  setUiDensity, setSidebarCollapsed, initUiPreferences, importPageHeader,
  UI_DENSITY_STORAGE_KEY, UI_SIDEBAR_STORAGE_KEY,
  currentCustomerValues, diffCustomerPayload, validateCustomerForm,
  customerDetailMarkup, customerFormMarkup, setUser: (user) => { state.user = user; },
};

if (!globalThis.__MDM_TEST_NO_AUTO_INIT__) initMdm();
