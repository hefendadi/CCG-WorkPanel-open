/* Reference Masters V1 unified workspace. Loaded before mdm.js; globals resolve at call time. */
globalThis.MDMReferenceMasters = (() => {
  const CONFIG = {
    channels: {
      label: 'Channel', name: 'channel_name', code: 'channel_code', controlled: false,
      description: '客户渠道基础主档。编码全局唯一且停用后不可复用。',
      fields: [
        ['channel_code', 'Channel Code', true, 'text'], ['channel_name', 'Channel 名称', true, 'text'],
        ['channel_type', 'Channel 类型', false, 'text'], ['sort_order', '排序值', false, 'number'],
      ],
    },
    salesreps: {
      label: 'SalesRep', name: 'salesrep_name', code: 'employee_code', controlled: false,
      description: '销售业务担当身份。employee_code 是可选外部员工编号，永久身份由系统唯一编号承担。',
      fields: [
        ['salesrep_name', '担当名称', true, 'text'], ['employee_code', '员工编号（可选）', false, 'text'],
      ],
    },
    regions: {
      label: 'Region', name: 'region_name', code: 'region_code', controlled: true,
      description: '受控大区字典。Region 与 Province 在 V1 中相互独立。',
      fields: [['region_code', 'Region Code', true, 'text'], ['region_name', 'Region 名称', true, 'text']],
    },
    provinces: {
      label: 'Province', name: 'province_name', code: 'province_code', controlled: true,
      description: '受控省份字典。不建立强制 Province → Region 归属。',
      fields: [['province_code', 'Province Code', true, 'text'], ['province_name', 'Province 名称', true, 'text']],
    },
  };
  const local = { resource: null, page: null, filters: null, item: null, editing: false };

  function config(resource) { return CONFIG[resource]; }
  function basePath(resource) { return `/mdm/references/${resource}`; }
  function tabs(active) {
    return `<nav class="ccg-local-tabs" aria-label="Reference Data 资源">${Object.entries(CONFIG).map(([key, item]) =>
      `<a href="${basePath(key)}" data-nav="${basePath(key)}" class="ccg-local-tab ${key === active ? 'active' : ''}" ${key === active ? 'aria-current="page"' : ''}>${item.label}</a>`
    ).join('')}</nav>`;
  }
  function headerMarkup(title, description, actions = '') {
    return `<header class="ccg-page-header"><div><h1>${esc(title)}</h1><p>${esc(description)}</p></div><div class="ccg-page-actions">${actions}</div></header>`;
  }
  function statusBadge(status) {
    return `<span class="ccg-status ${status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(status)}</span>`;
  }
  function paramsFrom(search = '') {
    const params = new URLSearchParams(search);
    const page = Number(params.get('page')); const size = Number(params.get('page_size'));
    return {
      search: params.get('search') || '', status: params.get('status') === 'INACTIVE' ? 'INACTIVE' : 'ACTIVE',
      sort: params.get('sort') || 'updated_at', order: params.get('order') === 'asc' ? 'asc' : 'desc',
      page: Number.isInteger(page) && page > 0 ? page : 1,
      page_size: [25, 50, 100].includes(size) ? size : 50,
    };
  }
  function queryString(filters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => { if (value !== '') params.set(key, String(value)); });
    return params.toString();
  }
  function setUrl(resource, filters, replace = true) {
    const query = queryString(filters);
    history[replace ? 'replaceState' : 'pushState']({}, '', `${basePath(resource)}${query ? `?${query}` : ''}`);
  }
  function codeValue(item, cfg) { return item[cfg.code] || '—'; }
  function pagination(page) {
    const from = page.total ? (page.page - 1) * page.page_size + 1 : 0;
    const to = Math.min(page.page * page.page_size, page.total);
    return `<footer class="ccg-pagination"><span class="ccg-tabular">第 ${from}–${to} 条，共 ${page.total} 条</span><div class="ccg-pagination-controls">
      <label>每页 <select class="ccg-select" data-action="reference-page-size"><option value="25" ${page.page_size === 25 ? 'selected' : ''}>25</option><option value="50" ${page.page_size === 50 ? 'selected' : ''}>50</option><option value="100" ${page.page_size === 100 ? 'selected' : ''}>100</option></select></label>
      <button class="ccg-button ccg-button-secondary" type="button" data-action="reference-page-prev" ${page.page <= 1 ? 'disabled' : ''}>上一页</button>
      <span class="ccg-tabular">第 ${page.page} / ${page.pages || 1} 页</span>
      <button class="ccg-button ccg-button-secondary" type="button" data-action="reference-page-next" ${page.page >= page.pages ? 'disabled' : ''}>下一页</button></div></footer>`;
  }
  function listMarkup(resource, page) {
    const cfg = config(resource);
    const rows = page.items.map((item) => `<tr>
      <td class="ccg-reference-name-cell"><a class="ccg-reference-name" href="${basePath(resource)}/${encodeURIComponent(item.stable_id)}" data-nav="${basePath(resource)}/${encodeURIComponent(item.stable_id)}">${esc(displayValue(item[cfg.name]))}</a></td>
      <td class="ccg-mono">${esc(codeValue(item, cfg))}</td><td class="ccg-reference-status">${statusBadge(item.status)}</td><td class="ccg-mono ccg-tabular ccg-col-time">${esc(formatTime(item.updated_at))}</td>
    </tr>`).join('');
    const actions = canWrite() ? `<button class="ccg-button ccg-button-primary" type="button" data-action="reference-new">+ 新建 ${cfg.label}</button>` : '';
    const density = document.getElementById('app-shell')?.dataset.density === 'compact' ? 'compact' : 'comfortable';
    const empty = `<tr><td class="ccg-table-empty" colspan="4">${stateMarkup('empty', `没有匹配的 ${cfg.label}`, '请调整搜索或状态筛选。', '重置筛选', 'reference-filters-reset')}</td></tr>`;
    return `<div class="ccg-reference-page">${headerMarkup(`${cfg.label} 主档`, cfg.description, actions)}${tabs(resource)}
      ${!canWrite() ? '<div class="ccg-readonly-banner">当前为只读模式。你可以查看、搜索和筛选，但不能新建、编辑或改变状态。</div>' : ''}
      <section class="ccg-panel ccg-master-list ccg-reference-workspace"><form id="reference-filter-form" class="ccg-master-filter ccg-reference-filters">
        <label class="ccg-filter-field ccg-filter-field-search"><span class="ccg-filter-label">搜索名称 / Code</span><input class="ccg-input" name="search" value="${esc(local.filters.search)}" placeholder="输入前缀搜索"></label>
        <label class="ccg-filter-field"><span class="ccg-filter-label">状态</span><select class="ccg-select" name="status"><option value="ACTIVE" ${local.filters.status === 'ACTIVE' ? 'selected' : ''}>有效</option><option value="INACTIVE" ${local.filters.status === 'INACTIVE' ? 'selected' : ''}>停用</option></select></label>
        <label class="ccg-filter-field"><span class="ccg-filter-label">排序</span><select class="ccg-select" name="sort-order"><option value="updated_at:desc" ${local.filters.sort === 'updated_at' && local.filters.order === 'desc' ? 'selected' : ''}>最近更新</option><option value="${cfg.name}:asc" ${local.filters.sort === cfg.name ? 'selected' : ''}>名称</option><option value="${cfg.code}:asc" ${local.filters.sort === cfg.code ? 'selected' : ''}>Code</option></select></label>
        <div class="ccg-reference-filter-actions"><button class="ccg-button ccg-button-primary" type="submit">查询</button><button type="button" class="ccg-button ccg-button-secondary" data-action="reference-filters-reset">重置</button></div>
      </form><div class="ccg-master-table-tools ccg-reference-table-tools"><span class="ccg-tabular">${cfg.label} 列表 · ${page.total} 条</span><div class="ccg-density-toggle" aria-label="表格密度"><button class="ccg-density-option ${density === 'comfortable' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="comfortable" aria-pressed="${density === 'comfortable'}">舒适</button><button class="ccg-density-option ${density === 'compact' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="compact" aria-pressed="${density === 'compact'}">紧凑</button></div></div>
      <div class="ccg-table-scroll" role="region" aria-label="${cfg.label} 列表"><table class="ccg-table ccg-reference-table"><thead><tr><th>业务名称</th><th>${resource === 'salesreps' ? '员工编号（可选）' : 'Code'}</th><th>状态</th><th>更新时间</th></tr></thead><tbody>${rows || empty}</tbody></table></div>${pagination(page)}</section></div>`;
  }
  async function renderList(resource, sequence = state.routeSequence) {
    local.resource = resource; local.filters = paramsFrom(location.search); local.item = null;
    setTopbar(`基础主档 · ${config(resource).label}`, 'references'); setContent(loadingMarkup('table'));
    try {
      const page = await mdmFetch(`/${resource}?${queryString(local.filters)}`);
      if (sequence !== state.routeSequence) return;
      local.page = page; setContent(listMarkup(resource, page));
    } catch (error) { const cfg = config(resource); setContent(`<div class="ccg-reference-page">${headerMarkup(`${cfg.label} 主档`, cfg.description)}${tabs(resource)}${stateMarkup('error', '基础主档加载失败', error.message, '重试', 'reference-retry')}</div>`); }
  }
  function detailMarkup(resource, item) {
    const cfg = config(resource);
    const lifecycleClass = item.status === 'ACTIVE' ? 'ccg-button-danger-ghost' : 'ccg-button-success';
    const actions = canWrite() ? `<button class="ccg-button ccg-button-primary" type="button" data-action="reference-edit">编辑</button><button class="ccg-button ${lifecycleClass}" type="button" data-action="reference-lifecycle" data-lifecycle="${item.status === 'ACTIVE' ? 'deactivate' : 'activate'}">${item.status === 'ACTIVE' ? '停用' : '启用'}</button>` : '';
    const definitions = cfg.fields.map(([field, label, _required, type]) => {
      const valueClass = field === cfg.code ? 'ccg-mono' : type === 'number' ? 'ccg-tabular' : '';
      return `<div class="${valueClass}"><dt>${esc(label)}</dt><dd>${esc(displayValue(item[field]))}</dd></div>`;
    }).join('');
    return `<div class="ccg-reference-page ccg-reference-detail">${headerMarkup(`${item[cfg.name]}`, cfg.description, actions)}${tabs(resource)}
      ${!canWrite() ? '<div class="ccg-readonly-banner">当前为只读模式：你可以查看详情，但不能编辑或改变状态。</div>' : ''}
      <section class="ccg-panel ccg-reference-detail-panel"><div class="ccg-reference-detail-summary"><div><span class="ccg-filter-label">业务 Code</span><strong class="ccg-mono">${esc(codeValue(item, cfg))}</strong></div><div><span class="ccg-filter-label">状态</span>${statusBadge(item.status)}</div><div><span class="ccg-filter-label">更新时间</span><strong class="ccg-mono ccg-tabular">${esc(formatTime(item.updated_at))}</strong></div></div>
      <div class="ccg-reference-detail-grid"><section><h2>业务字段</h2><dl class="ccg-definition-grid">${definitions}</dl></section>
      <section><h2>系统信息</h2><dl class="ccg-definition-grid"><div class="ccg-mono"><dt>系统唯一编号</dt><dd>${esc(item.stable_id)}</dd></div><div><dt>状态</dt><dd>${statusLabel(item.status)}</dd></div><div class="ccg-tabular"><dt>创建时间</dt><dd>${esc(formatTime(item.created_at))}</dd></div><div class="ccg-tabular"><dt>更新时间</dt><dd>${esc(formatTime(item.updated_at))}</dd></div></dl></section></div></section></div>`;
  }
  async function loadDetail(resource, stableId, sequence = state.routeSequence, toast = '') {
    local.resource = resource; setTopbar(`${config(resource).label} 详情`, 'references'); setContent(loadingMarkup());
    try {
      const item = await mdmFetch(`/${resource}/${encodeURIComponent(stableId)}`);
      if (sequence !== state.routeSequence) return;
      local.item = item; local.editing = false; setContent(detailMarkup(resource, item)); if (toast) showToast(toast);
    } catch (error) { const cfg = config(resource); setContent(`<div class="ccg-reference-page">${headerMarkup(`${cfg.label} 详情`, cfg.description)}${tabs(resource)}${stateMarkup('error', '基础主档加载失败', error.message, '返回列表', 'reference-back')}</div>`); }
  }
  function formMarkup(resource, item = null) {
    const cfg = config(resource); const editing = Boolean(item);
    if (!canWrite()) return `<div class="ccg-reference-page">${headerMarkup(`${cfg.label} · 只读`, cfg.description)}${tabs(resource)}${stateMarkup('access', '只读权限', 'Operator 只能查看基础主档；后端也会拒绝所有写请求。', '返回列表', 'reference-back')}</div>`;
    const inputs = cfg.fields.map(([field, label, required, type]) => {
      const immutableCode = editing && field === cfg.code && resource !== 'salesreps';
      const inputClass = field === cfg.code ? 'ccg-mono' : type === 'number' ? 'ccg-tabular' : '';
      return `<label class="ccg-form-field"><span class="ccg-form-label ${required ? 'required' : ''}">${esc(label)}</span><input class="${inputClass}" name="${field}" type="${type}" ${required ? 'required' : ''} ${immutableCode ? 'disabled aria-describedby="immutable-code-help"' : ''} maxlength="${type === 'text' ? '128' : ''}" value="${esc(item?.[field] ?? '')}">${immutableCode ? '<small id="immutable-code-help" class="ccg-form-help">Code 创建后不可修改或复用</small>' : ''}</label>`;
    }).join('');
    const confirmation = !editing && cfg.controlled ? `<label class="confirmation-check span-2"><input name="confirm_create" type="checkbox" required><span>我确认这是受控基础字典的新业务值，Code 已核实且创建后不可复用。</span></label>` : '';
    return `<div class="ccg-reference-page ccg-reference-form-page">${headerMarkup(editing ? `编辑 ${cfg.label}` : `新建 ${cfg.label}`, cfg.description)}${tabs(resource)}
      <form id="reference-form" class="ccg-master-form ccg-reference-form"><section class="ccg-panel ccg-master-form-section ccg-reference-form-section"><div class="ccg-panel-header"><h2>业务字段</h2></div><div class="ccg-form-grid">${inputs}${confirmation}</div><div id="reference-form-error" class="inline-error" hidden></div><div class="ccg-form-actions"><button type="button" class="ccg-button ccg-button-secondary" data-action="reference-cancel">取消</button><button class="ccg-button ccg-button-primary" type="submit">${editing ? '保存修改' : `创建 ${cfg.label}`}</button></div></section>
      <aside class="ccg-panel ccg-master-form-summary ccg-reference-governance"><div class="ccg-panel-header"><div><h2>治理边界</h2><p>系统唯一编号、状态和时间戳由系统维护。Code 全局唯一，停用后也不能复用。</p></div></div>${resource === 'salesreps' ? '<div class="banner banner-info">employee_code 可留空，不承担永久系统身份职责。</div>' : ''}${cfg.controlled ? '<div class="banner banner-warning">这是受控字典，创建前必须明确确认。</div>' : ''}</aside></form></div>`;
  }
  function renderForm(resource, item = null) {
    local.resource = resource; local.editing = Boolean(item); if (!item) local.item = null;
    setTopbar(`${item ? '编辑' : '新建'} ${config(resource).label}`, 'references'); setContent(formMarkup(resource, item));
  }
  async function submitForm(form) {
    const cfg = config(local.resource); const data = new FormData(form); const payload = {};
    cfg.fields.forEach(([field, _label, required, type]) => {
      if (local.editing && field === cfg.code && local.resource !== 'salesreps') return;
      const raw = String(data.get(field) ?? '').trim();
      if (raw !== '' || required) payload[field] = type === 'number' ? Number(raw) : raw;
      else if (local.editing) payload[field] = null;
    });
    if (!local.editing && cfg.controlled) payload.confirm_create = data.get('confirm_create') === 'on';
    const errorBox = document.getElementById('reference-form-error'); errorBox.hidden = true;
    try {
      const item = await mdmFetch(local.editing ? `/${local.resource}/${encodeURIComponent(local.item.stable_id)}` : `/${local.resource}`, { method: local.editing ? 'PATCH' : 'POST', body: JSON.stringify(payload) });
      state.references = null; navigate(`${basePath(local.resource)}/${encodeURIComponent(item.stable_id)}`); showToast(local.editing ? '基础主档已更新。' : '基础主档已创建。');
    } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
  }
  function lifecycle(action) {
    if (!local.item || !canWrite()) return;
    const deactivating = action === 'deactivate'; const cfg = config(local.resource);
    const confirm = document.getElementById('dialog-confirm');
    document.getElementById('dialog-title').textContent = `${deactivating ? '停用' : '启用'} ${local.item[cfg.name]}？`;
    document.getElementById('dialog-copy').textContent = deactivating
      ? `停用后不能用于新的业务关系。若仍有关联记录，系统将保留关系且不会级联修改；本次确认允许在警告后继续停用。`
      : '启用后可重新用于新的业务关系。';
    document.getElementById('dialog-error').hidden = true; confirm.textContent = deactivating ? '确认停用' : '确认启用';
    confirm.className = `ccg-button ${deactivating ? 'ccg-button-danger' : 'ccg-button-success'}`;
    state.dialogAction = async () => {
      confirm.disabled = true;
      try {
        await mdmFetch(`/${local.resource}/${encodeURIComponent(local.item.stable_id)}/${action}`, { method: 'POST', body: JSON.stringify(deactivating ? { confirm_references: true } : {}) });
        closeDialog(); state.references = null; await loadDetail(local.resource, local.item.stable_id, state.routeSequence, deactivating ? '基础主档已停用，既有关系保持不变。' : '基础主档已启用。');
      } catch (error) { const box = document.getElementById('dialog-error'); box.textContent = error.message; box.hidden = false; confirm.disabled = false; }
    };
    document.getElementById('confirm-layer').hidden = false; confirm.disabled = false; confirm.focus();
  }
  function applyFilters(form) {
    const data = new FormData(form); const [sort, order] = String(data.get('sort-order')).split(':');
    local.filters = { ...local.filters, search: String(data.get('search') || '').trim(), status: String(data.get('status')), sort, order, page: 1 };
    setUrl(local.resource, local.filters, false); renderList(local.resource, state.routeSequence);
  }
  function changePage(page) { if (!local.page || page < 1 || page > local.page.pages) return; local.filters.page = page; setUrl(local.resource, local.filters); renderList(local.resource, state.routeSequence); }
  function handleAction(action, target) {
    if (action === 'reference-new') navigate(`${basePath(local.resource)}/new`);
    else if (action === 'reference-edit') renderForm(local.resource, local.item);
    else if (action === 'reference-cancel') local.item ? loadDetail(local.resource, local.item.stable_id, state.routeSequence) : navigate(basePath(local.resource));
    else if (action === 'reference-back') navigate(basePath(local.resource));
    else if (action === 'reference-retry') renderList(local.resource, state.routeSequence);
    else if (action === 'reference-filters-reset') navigate(basePath(local.resource));
    else if (action === 'reference-page-prev') changePage(local.page.page - 1);
    else if (action === 'reference-page-next') changePage(local.page.page + 1);
    else if (action === 'reference-lifecycle') lifecycle(target.dataset.lifecycle);
    else return false;
    return true;
  }
  function handleSubmit(event) { if (event.target.id === 'reference-filter-form') { event.preventDefault(); applyFilters(event.target); return true; } if (event.target.id === 'reference-form') { event.preventDefault(); submitForm(event.target); return true; } return false; }
  function handleChange(event) { if (event.target.dataset.action !== 'reference-page-size') return false; local.filters.page_size = Number(event.target.value); local.filters.page = 1; setUrl(local.resource, local.filters); renderList(local.resource, state.routeSequence); return true; }
  function handleInput() { return false; }
  return { renderList, loadDetail, renderForm, handleAction, handleSubmit, handleChange, handleInput, test: { CONFIG, paramsFrom, queryString, listMarkup, detailMarkup, formMarkup } };
})();
