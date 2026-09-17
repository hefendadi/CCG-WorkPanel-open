/* Product + SKU Master V1 workspace. Loaded before mdm.js; globals resolve at call time. */
globalThis.MDMProductSKU = (() => {
  const PRODUCT_FIELDS = ['product_code', 'product_name', 'brand'];
  const SKU_FIELDS = [
    'sku_name', 'product_group', 'product_form', 'origin', 'category_l1', 'category_l2',
    'category_l3', 'category_l4', 'short_name', 'category_extra', 'case_pack',
    'source_product_code', 'source_created_at',
  ];
  const SIGNIFICANT_SKU_FIELDS = ['category_l3', 'category_l4', 'case_pack'];
  const SELECTOR_DEBOUNCE_MS = 250;
  const SELECTOR_MIN_CHARS = 2;
  const SELECTOR_LIMIT = 20;
  const SKU_LABELS = {
    sku_code: 'SKU编码', sku_name: 'SKU名称', product_group: 'ERP分组', product_form: '产品形式',
    origin: '产地', category_l1: '一级分类', category_l2: '二级分类', category_l3: '三级分类',
    category_l4: '四级分类', short_name: '简称', category_extra: '扩展分类', case_pack: '箱规',
    source_product_code: '合并编码', source_created_at: '来源创建日期', barcode: '条码',
    product: '所属 Product', status: '状态', stable_id: '系统唯一编号',
    created_at: '系统创建时间', updated_at: '系统更新时间',
  };
  const SKU_LIST_STORAGE_KEY = 'mdm.sku.visible-columns.v1';
  const SKU_LIST_COLUMNS = [
    'sku_code', 'sku_name', 'product', 'product_group', 'category_l1', 'category_l2',
    'case_pack', 'status', 'product_form', 'origin', 'category_l3', 'category_l4',
    'short_name', 'category_extra', 'source_product_code', 'source_created_at', 'barcode',
    'stable_id', 'created_at', 'updated_at',
  ];
  const DEFAULT_SKU_LIST_COLUMNS = [
    'sku_code', 'sku_name', 'product', 'product_group', 'category_l1', 'category_l2',
    'case_pack', 'status',
  ];
  const SKU_DYNAMIC_FILTERS = {
    product_group: { label: 'ERP分组', type: 'multi' },
    product_form: { label: '产品形式', type: 'multi' },
    origin: { label: '产地', type: 'multi' },
    category_l1: { label: '一级分类', type: 'multi' },
    category_l2: { label: '二级分类', type: 'multi' },
    category_l3: { label: '三级分类', type: 'multi' },
    category_l4: { label: '四级分类', type: 'multi' },
    category_extra: { label: '扩展分类', type: 'multi' },
    sku_code: { label: 'SKU编码', type: 'text' },
    sku_name: { label: 'SKU名称', type: 'text' },
    short_name: { label: '简称', type: 'text' },
    product_search: { label: 'Product code / name', type: 'text' },
    source_product_code: { label: 'source_product_code / 合并编码', type: 'text' },
    case_pack: { label: '箱规', type: 'number' },
  };
  const local = {
    productPage: null, skuPage: null, product: null, sku: null,
    productFilters: null, skuFilters: null, editing: false,
    skuVisibleColumns: null, skuFilterOptions: {}, skuDraftFilterFields: new Set(),
    selectedProductFilter: null, selectedSkuFilter: null,
    selectorTimers: {}, selectorRequests: {}, skuFinder: null,
  };

  function paramsFrom(search, kind) {
    const params = new URLSearchParams(search || '');
    const size = Number(params.get('page_size'));
    const page = Number(params.get('page'));
    const status = params.get('status');
    const base = {
      search: params.get('search') || '',
      status: kind === 'sku'
        ? (['ACTIVE', 'INACTIVE', 'ALL'].includes(status) ? status : 'ALL')
        : (status === 'INACTIVE' ? 'INACTIVE' : 'ACTIVE'),
      page: Number.isInteger(page) && page > 0 ? page : 1,
      page_size: [25, 50, 100].includes(size) ? size : 50,
      sort: params.get('sort') || 'updated_at', order: params.get('order') === 'asc' ? 'asc' : 'desc',
    };
    if (kind === 'sku') {
      base.barcode = params.get('barcode') || '';
      base.product_stable_id = params.get('product_stable_id') || '';
      base.sku_stable_id = params.get('sku_stable_id') || '';
      Object.entries(SKU_DYNAMIC_FILTERS).forEach(([field, definition]) => {
        base[field] = definition.type === 'multi' ? params.getAll(field) : (params.get(field) || '');
      });
    }
    return base;
  }

  function queryString(filters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        value.filter((item) => item !== '').forEach((item) => params.append(key, String(item)));
      } else if (value !== '' && value !== null && value !== undefined) params.set(key, String(value));
    });
    return params.toString();
  }

  function setUrl(kind, filters, replace = true) {
    const path = kind === 'product' ? '/mdm/products' : '/mdm/skus';
    const query = queryString(filters);
    history[replace ? 'replaceState' : 'pushState']({}, '', `${path}${query ? `?${query}` : ''}`);
  }

  function pagination(kind, page) {
    const from = page.total ? (page.page - 1) * page.page_size + 1 : 0;
    const to = Math.min(page.page * page.page_size, page.total);
    return `<footer class="ccg-pagination"><span>第 ${from}–${to} 条，共 ${page.total} 条</span>
      <div class="ccg-pagination-controls">
        <label>每页 <select class="ccg-select" data-action="${kind}-page-size"><option ${page.page_size === 25 ? 'selected' : ''}>25</option><option ${page.page_size === 50 ? 'selected' : ''}>50</option><option ${page.page_size === 100 ? 'selected' : ''}>100</option></select></label>
        <button class="ccg-button ccg-button-secondary" type="button" data-action="${kind}-page-prev" ${page.page <= 1 ? 'disabled' : ''}>上一页</button>
        <span class="ccg-tabular">第 ${page.page} / ${page.pages || 1} 页</span>
        <button class="ccg-button ccg-button-secondary" type="button" data-action="${kind}-page-next" ${page.page >= page.pages ? 'disabled' : ''}>下一页</button>
      </div></footer>`;
  }

  function masterPageHeader(title, description, actions = '') {
    return `<header class="ccg-page-header"><div><h1>${esc(title)}</h1><p>${esc(description)}</p></div><div class="ccg-page-actions">${actions}</div></header>`;
  }

  function productListMarkup(page) {
    const density = document.getElementById('app-shell')?.dataset.density === 'compact' ? 'compact' : 'comfortable';
    const rows = page.items.map((item) => `<tr>
      <td><a class="ccg-table-code" href="/mdm/products/${encodeURIComponent(item.stable_id)}" data-nav="/mdm/products/${encodeURIComponent(item.stable_id)}">${esc(item.product_code)}</a></td>
      <td>${esc(displayValue(item.product_name))}</td><td>${esc(displayValue(item.brand))}</td>
      <td class="ccg-col-count ccg-tabular"><button class="ccg-table-link" type="button" data-nav="/mdm/products/${encodeURIComponent(item.stable_id)}">${item.sku_count}</button></td>
      <td class="ccg-col-status"><span class="ccg-status ${item.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(item.status)}</span></td>
      <td class="ccg-col-time ccg-tabular ccg-mono">${esc(formatTime(item.updated_at))}</td>
    </tr>`).join('');
    const actions = canWrite() ? '<button class="ccg-button ccg-button-secondary" type="button" data-action="product-import">批量导入商品</button><button class="ccg-button ccg-button-primary" type="button" data-action="product-new">新建 Product</button>' : '';
    const readonly = canWrite() ? '' : '<div class="ccg-readonly-banner">当前为只读模式。可查看、搜索和筛选。</div>';
    const empty = `<tr><td class="ccg-table-empty" colspan="6">${stateMarkup('empty', '没有匹配的 Product', '请调整搜索或筛选条件。', '重置筛选', 'product-filters-reset')}</td></tr>`;
    return `<div class="ccg-product-list"><header class="ccg-page-header"><div><h1>商品主档</h1><p>Product 是稳定的业务商品实体；一个 Product 可包含零个、一个或多个 SKU。</p></div><div class="ccg-page-actions">${actions}</div></header>${readonly}
      <section class="ccg-panel ccg-master-list ccg-product-table-container">
        <form id="product-filter-form" class="ccg-master-filter ccg-product-toolbar">
          <div class="ccg-product-filters">
            <label class="ccg-filter-field ccg-filter-field-search"><span class="ccg-filter-label">搜索商品或 SKU</span><input class="ccg-input" name="search" value="${esc(local.productFilters.search)}" placeholder="输入编码或名称"></label>
            <label class="ccg-filter-field"><span class="ccg-filter-label">状态</span><select class="ccg-select" name="status"><option value="ACTIVE" ${local.productFilters.status === 'ACTIVE' ? 'selected' : ''}>有效</option><option value="INACTIVE" ${local.productFilters.status === 'INACTIVE' ? 'selected' : ''}>停用</option></select></label>
            <label class="ccg-filter-field"><span class="ccg-filter-label">排序</span><select class="ccg-select" name="sort-order"><option value="updated_at:desc" ${local.productFilters.sort === 'updated_at' && local.productFilters.order === 'desc' ? 'selected' : ''}>最近更新</option><option value="product_code:asc" ${local.productFilters.sort === 'product_code' && local.productFilters.order === 'asc' ? 'selected' : ''}>Product Code</option><option value="product_name:asc" ${local.productFilters.sort === 'product_name' && local.productFilters.order === 'asc' ? 'selected' : ''}>Product 名称</option></select></label>
            <button class="ccg-button ccg-button-primary" type="submit">查询</button><button type="button" class="ccg-button ccg-button-secondary" data-action="product-filters-reset">重置</button>
          </div>
        </form>
        <div class="ccg-master-table-tools"><span class="ccg-tabular">Product 列表 · ${page.total} 条</span>
          <div class="ccg-density-toggle" aria-label="表格密度">
            <button class="ccg-density-option ${density === 'comfortable' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="comfortable" aria-pressed="${density === 'comfortable'}">舒适</button>
            <button class="ccg-density-option ${density === 'compact' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="compact" aria-pressed="${density === 'compact'}">紧凑</button>
          </div>
        </div>
        <div class="ccg-table-scroll"><table class="ccg-table ccg-product-table"><thead><tr><th>Product Code</th><th>Product 名称</th><th>品牌</th><th class="ccg-col-count">SKU 数</th><th class="ccg-col-status">状态</th><th>更新时间</th></tr></thead><tbody>${rows || empty}</tbody></table></div>
        ${pagination('product', page)}
      </section></div>`;
  }

  async function renderProducts(sequence = state.routeSequence) {
    setTopbar('商品主档', 'products');
    setContent(loadingMarkup('table'));
    local.productFilters = paramsFrom(location.search, 'product');
    try {
      const page = await mdmFetch(`/products?${queryString(local.productFilters)}`);
      if (sequence !== state.routeSequence) return;
      local.productPage = page;
      setContent(productListMarkup(page));
    } catch (error) {
      setContent(`<div class="ccg-product-list"><header class="ccg-page-header"><div><h1>商品主档</h1><p>Product 是稳定的业务商品实体；一个 Product 可包含零个、一个或多个 SKU。</p></div></header>${stateMarkup('error', 'Product 加载失败', error.message, '重试', 'product-retry')}</div>`);
    }
  }

  function productDetailMarkup(product) {
    const actions = canWrite() ? `<button class="ccg-button ccg-button-primary" type="button" data-action="product-edit">编辑</button><button class="ccg-button ${product.status === 'ACTIVE' ? 'ccg-button-danger-ghost' : 'ccg-button-success'}" type="button" data-action="product-lifecycle" data-lifecycle="${product.status === 'ACTIVE' ? 'deactivate' : 'activate'}">${product.status === 'ACTIVE' ? '停用' : '启用'}</button>` : '';
    const children = (product.skus || []).map((sku) => `<tr>
      <td><a class="ccg-table-code" href="/mdm/skus/${encodeURIComponent(sku.stable_id)}" data-nav="/mdm/skus/${encodeURIComponent(sku.stable_id)}">${esc(sku.sku_code)}</a></td>
      <td>${esc(displayValue(sku.sku_name))}</td><td>${esc(displayValue(sku.specification))}</td><td>${esc(displayValue(sku.net_weight))}</td>
      <td class="ccg-numeric">${esc(displayValue(sku.case_pack))}</td><td class="ccg-mono">${esc(displayValue(sku.barcode))}</td><td class="ccg-col-status"><span class="ccg-status ${sku.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(sku.status)}</span></td>
      <td>${canWrite() ? `<button class="ccg-table-link" type="button" data-action="sku-reassign" data-sku="${esc(sku.stable_id)}">调整归属</button>` : ''}</td>
    </tr>`).join('');
    const status = `<span class="ccg-status ${product.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(product.status)}</span>`;
    const emptyChildren = `<tr><td class="ccg-table-empty" colspan="8">${stateMarkup('empty', '该 Product 当前没有 SKU', 'Product 可在没有 SKU 的情况下保持有效。')}</td></tr>`;
    return `<div class="ccg-master-detail ccg-product-detail"><header class="ccg-page-header"><div><div class="ccg-master-title-row"><h1>${esc(product.product_name)}</h1>${status}</div><p><span class="ccg-mono">${esc(product.product_code)}</span><span class="ccg-master-separator">·</span><span class="ccg-mono">${esc(product.stable_id)}</span><button class="copy-mini" type="button" data-action="copy" data-copy="${esc(product.stable_id)}" aria-label="复制系统唯一编号">□</button></p></div><div class="ccg-page-actions">${actions}</div></header>
      ${!canWrite() ? '<div class="ccg-readonly-banner">当前为只读权限。Product 字段、SKU 归属和状态不可修改。</div>' : ''}
      <div class="ccg-master-detail-grid"><section class="ccg-panel ccg-master-detail-section"><div class="ccg-panel-header"><h2>Product 字段</h2></div><dl class="ccg-definition-grid"><div><dt>Product Code</dt><dd class="ccg-mono">${esc(displayValue(product.product_code))}</dd></div><div><dt>Product 名称</dt><dd>${esc(displayValue(product.product_name))}</dd></div><div><dt>品牌</dt><dd>${esc(displayValue(product.brand))}</dd></div><div><dt>SKU 数</dt><dd class="ccg-tabular">${product.sku_count}</dd></div></dl></section>
      <section class="ccg-panel ccg-master-detail-section"><div class="ccg-panel-header"><h2>系统信息</h2></div><dl class="ccg-definition-grid"><div><dt>系统唯一编号</dt><dd class="ccg-mono">${esc(product.stable_id)}</dd></div><div><dt>状态</dt><dd>${status}</dd></div><div><dt>创建时间</dt><dd class="ccg-tabular">${esc(formatTime(product.created_at))}</dd></div><div><dt>更新时间</dt><dd class="ccg-tabular">${esc(formatTime(product.updated_at))}</dd></div></dl></section>
      <section class="ccg-panel ccg-master-child-panel"><div class="ccg-panel-header"><div><h2>SKU 子项（${product.sku_count}）</h2><p>Product 与 SKU 为一对多关系；零 SKU 不会自动改变 Product 状态。</p></div></div><div class="ccg-table-scroll" role="region" aria-label="Product 的 SKU 子项"><table class="ccg-table ccg-product-child-table"><thead><tr><th>SKU 编码</th><th>SKU 名称</th><th>规格</th><th>净重</th><th class="ccg-col-count">箱规</th><th>条码</th><th class="ccg-col-status">状态</th><th>操作</th></tr></thead><tbody>${children || emptyChildren}</tbody></table></div></section></div></div>`;
  }

  async function loadProductDetail(stableId, sequence = state.routeSequence, toast = '') {
    setTopbar('Product 详情', 'products'); setContent(loadingMarkup());
    try {
      const product = await mdmFetch(`/products/${encodeURIComponent(stableId)}`);
      if (sequence !== state.routeSequence) return;
      local.product = product; local.editing = false; setContent(productDetailMarkup(product));
      if (toast) showToast(toast);
    } catch (error) { setContent(`<div class="ccg-product-detail"><header class="ccg-page-header"><div><h1>Product 详情</h1><p>Product 主档及其 SKU 子项。</p></div></header>${stateMarkup('error', 'Product 加载失败', error.message, '返回 Product 列表', 'back-products')}</div>`); }
  }

  function productFormMarkup(product = null) {
    const editing = Boolean(product);
    const title = editing ? '编辑 Product' : '新建 Product';
    const copy = editing ? 'Product Code 可修正但必须全局唯一；stable_id 与系统字段不可编辑。' : '建立稳定业务商品实体；SKU 由既有数据流程提供，不支持手工新建。';
    if (!canWrite()) return `<div class="ccg-master-form-page"><header class="ccg-page-header"><div><h1>${title}</h1><p>${copy}</p></div></header>${stateMarkup('access', '只读权限', '当前角色只能查看 Product；MDM 服务也会拒绝写请求。', '返回 Product 列表', 'back-products')}</div>`;
    return `<div class="ccg-master-form-page"><header class="ccg-page-header"><div><h1>${title}</h1><p>${copy}</p></div></header>
      <form id="product-form" class="ccg-master-form"><section class="ccg-panel ccg-master-form-section"><div class="ccg-panel-header"><div><h2>业务字段</h2><p>Product Code 与名称为必填字段。</p></div></div><div class="ccg-form-grid">
        <label class="ccg-form-field"><span class="ccg-form-label required">Product Code</span><input class="ccg-input ccg-mono" name="product_code" required maxlength="128" value="${esc(product?.product_code || '')}"></label>
        <label class="ccg-form-field"><span class="ccg-form-label required">Product 名称</span><input class="ccg-input" name="product_name" required maxlength="255" value="${esc(product?.product_name || '')}"></label>
        <label class="ccg-form-field span-2"><span class="ccg-form-label">品牌</span><input class="ccg-input" name="brand" maxlength="128" value="${esc(product?.brand || '')}"></label>
      </div><div id="product-form-error" class="inline-error" hidden></div><div class="ccg-form-actions"><button type="button" class="ccg-button ccg-button-secondary" data-action="product-cancel-edit">取消</button><button class="ccg-button ccg-button-primary" type="submit">保存 Product</button></div></section>
      <aside class="ccg-panel ccg-master-form-summary"><div class="ccg-panel-header"><div><h2>治理边界</h2><p>系统唯一编号、状态与时间戳由系统维护。Product 不依赖代表 SKU。</p></div></div>${editing ? `<div class="ccg-capability-note"><strong>系统唯一编号</strong><span class="ccg-mono">${esc(product.stable_id)}</span></div>` : '<div class="ccg-capability-note"><strong>创建后生成</strong><span>系统唯一编号与有效状态</span></div>'}</aside></form></div>`;
  }

  async function renderProductForm(product = null) {
    setTopbar(product ? '编辑 Product' : '新建 Product', 'products');
    if (!product) local.product = null;
    local.editing = Boolean(product); setContent(productFormMarkup(product));
  }

  async function submitProduct(form) {
    const payload = Object.fromEntries(PRODUCT_FIELDS.map((field) => [field, String(new FormData(form).get(field) || '').trim()]));
    const errorBox = document.getElementById('product-form-error');
    try {
      const saved = await mdmFetch(local.product ? `/products/${encodeURIComponent(local.product.stable_id)}` : '/products', { method: local.product ? 'PATCH' : 'POST', body: JSON.stringify(payload) });
      local.product = saved; navigate(`/mdm/products/${encodeURIComponent(saved.stable_id)}`);
      const warning = saved.warnings?.[0]?.code === 'MDM_POSSIBLE_PRODUCT_MATCH' ? ' 存在同名 Product，请复核业务连续性。' : '';
      showToast(`${local.editing ? 'Product 已更新。' : 'Product 已创建。'}${warning}`);
    } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
  }

  async function loadSkuFilterOptions() {
    const payload = await mdmFetch('/skus/filter-options');
    local.skuFilterOptions = payload.fields || {};
    return local.skuFilterOptions;
  }

  async function loadSelectedFilterEntities() {
    const load = async (resource, stableId, fallback) => {
      if (!stableId) return null;
      try { return await mdmFetch(`/${resource}/${encodeURIComponent(stableId)}`); }
      catch (_error) { return fallback(stableId); }
    };
    [local.selectedProductFilter, local.selectedSkuFilter] = await Promise.all([
      load('products', local.skuFilters.product_stable_id, (stableId) => ({ stable_id: stableId, product_code: stableId, product_name: '已选择 Product' })),
      load('skus', local.skuFilters.sku_stable_id, (stableId) => ({ stable_id: stableId, sku_code: stableId, sku_name: '已选择 SKU', product: null })),
    ]);
  }

  async function searchProducts(query, activeOnly = false) {
    const statuses = activeOnly ? ['ACTIVE'] : ['ACTIVE', 'INACTIVE'];
    const pages = await Promise.all(statuses.map((status) => mdmFetch(
      `/products?search=${encodeURIComponent(query)}&status=${status}&page=1&page_size=${SELECTOR_LIMIT}&sort=product_code&order=asc`
    )));
    const seen = new Set();
    return pages.flatMap((page) => page.items).filter((item) => {
      if (seen.has(item.stable_id)) return false;
      seen.add(item.stable_id); return true;
    }).slice(0, SELECTOR_LIMIT);
  }

  async function searchSkus(query) {
    const page = await mdmFetch(
      `/skus?search=${encodeURIComponent(query)}&status=ALL&page=1&page_size=${SELECTOR_LIMIT}&sort=sku_code&order=asc`
    );
    return page.items.slice(0, SELECTOR_LIMIT);
  }

  function selectorResultsMarkup(kind, purpose, prefix, items) {
    if (!items.length) return '<div class="async-selector-state">没有匹配结果</div>';
    return items.map((item) => {
      const code = kind === 'product' ? item.product_code : item.sku_code;
      const name = kind === 'product' ? item.product_name : item.sku_name;
      const product = kind === 'sku' && item.product ? `${item.product.product_code} · ${item.product.product_name}` : '';
      return `<button type="button" role="option" data-action="selector-select" data-kind="${kind}" data-purpose="${purpose}" data-prefix="${prefix}" data-stable-id="${esc(item.stable_id)}" data-code="${esc(code)}" data-name="${esc(name)}" data-product="${esc(product)}"><strong>${esc(code)}</strong><span>${esc(name)}</span>${product ? `<small>${esc(product)}</small>` : ''}</button>`;
    }).join('');
  }

  async function runSelectorSearch(input, query, requestId) {
    const { selectorKind: kind, selectorPurpose: purpose, selectorPrefix: prefix } = input.dataset;
    const results = document.getElementById(`${prefix}-results`);
    if (!results) return;
    results.hidden = false;
    results.innerHTML = '<div class="async-selector-state">正在查询…</div>';
    try {
      let items = kind === 'product' ? await searchProducts(query, purpose === 'reassign') : await searchSkus(query);
      if (purpose === 'reassign' && input.dataset.excludeStableId) {
        items = items.filter((item) => item.stable_id !== input.dataset.excludeStableId);
      }
      if (local.selectorRequests[prefix] !== requestId || input.value.trim() !== query) return;
      results.innerHTML = selectorResultsMarkup(kind, purpose, prefix, items);
      input.setAttribute('aria-expanded', 'true');
    } catch (error) {
      if (local.selectorRequests[prefix] !== requestId) return;
      results.innerHTML = `<div class="async-selector-state async-selector-error">查询失败：${esc(error.message)}</div>`;
      input.setAttribute('aria-expanded', 'true');
    }
  }

  function scheduleSelectorSearch(input) {
    const prefix = input.dataset.selectorPrefix;
    const query = input.value.trim();
    const searchValue = document.getElementById(`${prefix}-search-value`);
    const selectedId = document.getElementById(`${prefix}-id`)?.value || '';
    if (input.dataset.selectorKind === 'sku' && input.dataset.selectorPurpose === 'filter' && !selectedId && searchValue) {
      searchValue.value = query;
    }
    clearTimeout(local.selectorTimers[prefix]);
    const results = document.getElementById(`${prefix}-results`);
    if (query.length < SELECTOR_MIN_CHARS) {
      local.selectorRequests[prefix] = (local.selectorRequests[prefix] || 0) + 1;
      if (results) {
        results.hidden = false;
        results.innerHTML = `<div class="async-selector-state">请至少输入 ${SELECTOR_MIN_CHARS} 个字符</div>`;
      }
      input.setAttribute('aria-expanded', 'true');
      return;
    }
    const requestId = (local.selectorRequests[prefix] || 0) + 1;
    local.selectorRequests[prefix] = requestId;
    local.selectorTimers[prefix] = setTimeout(() => runSelectorSearch(input, query, requestId), SELECTOR_DEBOUNCE_MS);
  }

  function loadSkuVisibleColumns() {
    try {
      const stored = JSON.parse(localStorage.getItem(SKU_LIST_STORAGE_KEY) || 'null');
      const valid = Array.isArray(stored) ? stored.filter((field) => SKU_LIST_COLUMNS.includes(field)) : [];
      if (valid.length) return new Set(valid);
    } catch (_error) { /* Invalid browser preference falls back to defaults. */ }
    return new Set(DEFAULT_SKU_LIST_COLUMNS);
  }

  function saveSkuVisibleColumns(columns) {
    const ordered = SKU_LIST_COLUMNS.filter((field) => columns.has(field));
    localStorage.setItem(SKU_LIST_STORAGE_KEY, JSON.stringify(ordered));
  }

  function skuColumnValue(sku, field) {
    if (field === 'sku_code') {
      return `<a class="ccg-table-code" href="/mdm/skus/${encodeURIComponent(sku.stable_id)}" data-nav="/mdm/skus/${encodeURIComponent(sku.stable_id)}">${esc(sku.sku_code)}</a>`;
    }
    if (field === 'sku_name') {
      return `<a class="ccg-sku-name-link" href="/mdm/skus/${encodeURIComponent(sku.stable_id)}" data-nav="/mdm/skus/${encodeURIComponent(sku.stable_id)}">${esc(displayValue(sku.sku_name, ''))}</a>`;
    }
    if (field === 'product') {
      return sku.product ? `<span class="ccg-mono">${esc(sku.product.product_code)}</span> · ${esc(sku.product.product_name)}` : '';
    }
    if (field === 'status') return `<span class="ccg-status ${sku.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(sku.status)}</span>`;
    if (field === 'created_at' || field === 'updated_at') return esc(sku[field] ? formatTime(sku[field]) : '');
    return esc(displayValue(sku[field], ''));
  }

  function skuColumnPickerMarkup(selected) {
    const options = SKU_LIST_COLUMNS.map((field) => `<label class="sku-column-option"><input type="checkbox" data-action="sku-column-toggle" value="${field}" ${selected.has(field) ? 'checked' : ''}><span>${SKU_LABELS[field]}</span></label>`).join('');
    return `<details class="sku-column-picker"><summary class="ccg-button ccg-button-secondary">显示字段 <span id="sku-visible-field-count">${selected.size}/${SKU_LIST_COLUMNS.length}</span></summary><div class="sku-column-menu"><p>选择 SKU 列表中显示的字段</p><div class="sku-column-options">${options}</div></div></details>`;
  }

  function skuColumnVisibility(field, selected) {
    return selected.has(field) ? '' : ' hidden';
  }

  function skuFilterPickerMarkup() {
    const choices = Object.entries(SKU_DYNAMIC_FILTERS).map(([field, definition]) =>
      `<button class="ccg-button ccg-button-secondary" type="button" data-action="sku-filter-add" data-field="${field}" ${local.skuDraftFilterFields.has(field) ? 'disabled' : ''}>${definition.label}</button>`
    ).join('');
    return `<details class="sku-filter-picker"><summary class="ccg-button ccg-button-secondary">＋ 添加筛选</summary><div class="sku-filter-menu"><p>选择一个 SKU 字段</p><div class="sku-filter-choices">${choices}</div></div></details>`;
  }

  function skuDynamicFilterMarkup(field) {
    const definition = SKU_DYNAMIC_FILTERS[field];
    if (!definition) return '';
    let control;
    if (definition.type === 'multi') {
      const selected = new Set(local.skuFilters[field] || []);
      const values = local.skuFilterOptions[field] || [];
      const options = values.map((value) => `<option value="${esc(value)}" ${selected.has(String(value)) ? 'selected' : ''}>${esc(value)}</option>`).join('');
      control = `<select class="ccg-select" name="${field}" multiple size="${Math.min(Math.max(values.length, 2), 4)}" aria-label="${definition.label}，可多选">${options}</select><small>可多选</small>`;
    } else if (definition.type === 'number') {
      control = `<input class="ccg-input" name="${field}" type="number" min="0.001" step="0.001" value="${esc(local.skuFilters[field] || '')}">`;
    } else {
      control = `<input class="ccg-input" name="${field}" value="${esc(local.skuFilters[field] || '')}">`;
    }
    return `<div class="sku-dynamic-filter"><label class="ccg-filter-field"><span class="ccg-filter-label">${definition.label}</span>${control}</label><button type="button" class="sku-filter-remove" data-action="sku-filter-remove" data-field="${field}" aria-label="删除 ${definition.label} 筛选">×</button></div>`;
  }

  function skuDynamicFiltersMarkup() {
    const filters = [...local.skuDraftFilterFields].map(skuDynamicFilterMarkup).join('');
    return `<div class="sku-dynamic-filter-bar"><div class="sku-dynamic-filter-heading"><strong>动态筛选</strong><span>不同条件默认 AND</span>${skuFilterPickerMarkup()}</div>${filters ? `<div class="sku-dynamic-filter-list">${filters}</div>` : ''}</div>`;
  }

  function selectorControlMarkup({ prefix, kind, purpose = 'filter', label, selected = null, query = '', excludeStableId = '' }) {
    const stableId = selected?.stable_id || '';
    const code = kind === 'product' ? selected?.product_code : selected?.sku_code;
    const name = kind === 'product' ? selected?.product_name : selected?.sku_name;
    const value = selected ? `${code} · ${name}` : query;
    const exactField = kind === 'product' ? 'product_stable_id' : 'sku_stable_id';
    const placeholder = kind === 'product' ? '输入 Product 编码或名称' : '输入 SKU 编码、名称或简称';
    const searchValue = kind === 'sku' && purpose === 'filter'
      ? `<input id="${prefix}-search-value" type="hidden" name="search" value="${esc(selected ? '' : query)}">`
      : '';
    return `<label class="ccg-form-field async-selector-field"><span class="ccg-form-label">${label}</span><span class="async-selector-input-wrap"><input id="${prefix}-input" value="${esc(value || '')}" placeholder="${placeholder}" autocomplete="off" spellcheck="false" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${prefix}-results" data-selector-kind="${kind}" data-selector-purpose="${purpose}" data-selector-prefix="${prefix}" data-exclude-stable-id="${esc(excludeStableId)}"><button type="button" class="async-selector-clear" data-action="selector-clear" data-kind="${kind}" data-purpose="${purpose}" data-prefix="${prefix}" ${stableId ? '' : 'hidden'} aria-label="清除 ${label}">×</button></span><input id="${prefix}-id" type="hidden" name="${exactField}" value="${esc(stableId)}">${searchValue}<span id="${prefix}-results" class="async-selector-results" role="listbox" hidden></span></label>`;
  }

  function skuListMarkup(page) {
    const density = document.getElementById('app-shell')?.dataset.density === 'compact' ? 'compact' : 'comfortable';
    const selected = local.skuVisibleColumns || loadSkuVisibleColumns();
    local.skuVisibleColumns = selected;
    const headers = SKU_LIST_COLUMNS.map((field) => `<th data-sku-column="${field}"${skuColumnVisibility(field, selected)}>${SKU_LABELS[field]}</th>`).join('');
    const rows = page.items.map((sku) => `<tr>${SKU_LIST_COLUMNS.map((field) => `<td data-sku-column="${field}"${skuColumnVisibility(field, selected)}>${skuColumnValue(sku, field)}</td>`).join('')}</tr>`).join('');
    const actions = canWrite() ? '<button class="ccg-button ccg-button-secondary" type="button" data-action="sku-import">批量导入商品</button>' : '';
    return `<div class="ccg-sku-list"><header class="ccg-page-header"><div><h1>SKU 主档</h1><p>SKU 编码永久不变；每个正式 SKU 必须且只能属于一个 Product。</p></div><div class="ccg-page-actions">${actions}</div></header>
      ${!canWrite() ? '<div class="ccg-readonly-banner">当前为只读权限，可查看、筛选和调整列显示。</div>' : ''}
      <section class="ccg-panel ccg-master-list ccg-sku-workspace">
      <form id="sku-filter-form" class="ccg-master-filter ccg-sku-toolbar">
        <div class="ccg-sku-filters">
          <div id="mdm-product-sku-finder" class="sku-unified-finder"></div>
          <input id="mdm-finder-product-id" type="hidden" name="product_stable_id" value="${esc(local.skuFilters.product_stable_id)}">
          <input id="mdm-finder-sku-id" type="hidden" name="sku_stable_id" value="${esc(local.skuFilters.sku_stable_id)}">
          <label class="ccg-filter-field"><span class="ccg-filter-label">状态</span><select class="ccg-select" name="status"><option value="ALL" ${local.skuFilters.status === 'ALL' ? 'selected' : ''}>全部</option><option value="ACTIVE" ${local.skuFilters.status === 'ACTIVE' ? 'selected' : ''}>有效</option><option value="INACTIVE" ${local.skuFilters.status === 'INACTIVE' ? 'selected' : ''}>停用</option></select></label>
          <div class="ccg-sku-filter-actions"><button class="ccg-button ccg-button-primary" type="submit">查询</button><button type="button" class="ccg-button ccg-button-secondary" data-action="sku-filters-reset">重置</button></div>
        </div>
        ${skuDynamicFiltersMarkup()}
      </form>
      <div class="ccg-master-table-tools ccg-sku-table-tools"><span class="ccg-tabular">SKU 列表 · ${page.total} 条</span><div class="ccg-sku-table-actions">
        ${skuColumnPickerMarkup(selected)}
        <div class="ccg-density-toggle" aria-label="表格密度">
          <button class="ccg-density-option ${density === 'comfortable' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="comfortable" aria-pressed="${density === 'comfortable'}">舒适</button>
          <button class="ccg-density-option ${density === 'compact' ? 'active' : ''}" type="button" data-action="ui-density" data-density-value="compact" aria-pressed="${density === 'compact'}">紧凑</button>
        </div>
      </div></div>
      <div class="ccg-table-scroll" role="region" aria-label="SKU 列表，可横向滚动查看字段" tabindex="0"><table class="ccg-table ccg-sku-table"><thead><tr>${headers}</tr></thead><tbody>${rows || `<tr><td id="sku-empty-cell" class="ccg-table-empty" colspan="${selected.size}">${stateMarkup('empty', '没有匹配的 SKU', '请调整搜索或筛选条件。')}</td></tr>`}</tbody></table></div>${pagination('sku', page)}</section></div>`;
  }

  function selectedFinderItem() {
    if (local.selectedSkuFilter) return { ...local.selectedSkuFilter, type: 'sku' };
    if (local.selectedProductFilter) return { ...local.selectedProductFilter, type: 'product' };
    return null;
  }

  function mountSkuFinder() {
    local.skuFinder = globalThis.UnifiedProductSkuFinder.mount('mdm-product-sku-finder', {
      fetchJson: mdmFetch,
      initialSelection: selectedFinderItem(),
      onSelect(item) {
        local.skuFilters.page = 1;
        local.skuFilters.search = '';
        local.skuFilters.barcode = '';
        local.skuFilters.product_stable_id = item.type === 'product' ? item.stable_id : '';
        local.skuFilters.sku_stable_id = item.type === 'sku' ? item.stable_id : '';
        setUrl('sku', local.skuFilters, false);
        renderSkus(state.routeSequence);
      },
      onClear() {
        local.skuFilters.page = 1;
        local.skuFilters.product_stable_id = '';
        local.skuFilters.sku_stable_id = '';
        setUrl('sku', local.skuFilters, false);
        renderSkus(state.routeSequence);
      },
    });
  }

  function setSkuContent(page) {
    setContent(skuListMarkup(page));
    mountSkuFinder();
  }

  async function renderSkus(sequence = state.routeSequence) {
    setTopbar('商品主档 · SKU', 'products'); setContent(loadingMarkup('table'));
    local.skuFilters = paramsFrom(location.search, 'sku');
    if (local.skuFilters.sku_stable_id) local.skuFilters.product_stable_id = '';
    local.skuDraftFilterFields = new Set(Object.keys(SKU_DYNAMIC_FILTERS).filter((field) => {
      const value = local.skuFilters[field];
      return Array.isArray(value) ? value.length > 0 : value !== '';
    }));
    try {
      await Promise.all([loadSkuFilterOptions(), loadSelectedFilterEntities()]);
      const page = await mdmFetch(`/skus?${queryString(local.skuFilters)}`);
      if (sequence !== state.routeSequence) return;
      local.skuPage = page; setSkuContent(page);
    } catch (error) { setContent(`${masterPageHeader('SKU 主档', 'SKU 编码永久不变；每个正式 SKU 必须且只能属于一个 Product。')}${stateMarkup('error', 'SKU 加载失败', error.message, '重试', 'sku-retry')}`); }
  }

  function skuDefinitions(sku) {
    const fields = ['sku_code', 'sku_name', 'product_group', 'product_form', 'origin', 'category_l1', 'category_l2', 'category_l3', 'category_l4', 'short_name', 'category_extra', 'case_pack', 'source_product_code', 'source_created_at'];
    return fields.map((field) => `<div><dt>${SKU_LABELS[field]}</dt><dd class="${['sku_code', 'source_product_code'].includes(field) ? 'ccg-mono' : ''} ${field === 'case_pack' ? 'ccg-tabular' : ''}">${esc(displayValue(sku[field], ''))}</dd></div>`).join('');
  }

  function skuDetailMarkup(sku) {
    const actions = canWrite() ? `<button class="ccg-button ccg-button-primary" data-action="sku-edit">编辑</button><button class="ccg-button ccg-button-secondary" data-action="sku-reassign" data-sku="${esc(sku.stable_id)}">调整 Product 归属</button><button class="ccg-button ${sku.status === 'ACTIVE' ? 'ccg-button-danger-ghost' : 'ccg-button-success'}" data-action="sku-lifecycle" data-lifecycle="${sku.status === 'ACTIVE' ? 'deactivate' : 'activate'}">${sku.status === 'ACTIVE' ? '停用' : '启用'}</button>` : '';
    const status = `<span class="ccg-status ${sku.status === 'ACTIVE' ? 'ccg-status-active' : 'ccg-status-inactive'}">${statusLabel(sku.status)}</span>`;
    return `<header class="ccg-page-header"><div><div class="ccg-master-title-row"><h1>${esc(sku.sku_name)}</h1>${status}</div><p><span class="ccg-mono">${esc(sku.sku_code)}</span><span class="ccg-master-title-separator">·</span><span class="ccg-mono">${esc(sku.stable_id)}</span></p></div><div class="ccg-page-actions">${actions}</div></header>
      ${!canWrite() ? '<div class="ccg-readonly-banner">当前为只读权限。SKU 字段、Product 归属和状态不可修改。</div>' : ''}
      <div class="ccg-master-detail-grid"><section class="ccg-panel ccg-master-detail-section ccg-master-detail-wide"><div class="ccg-panel-header"><div><h2>SKU 业务字段</h2><p>展示当前 SKU 的当前主档字段。</p></div></div><dl class="ccg-definition-grid sku-definition-grid">${skuDefinitions(sku)}</dl></section>
      <section class="ccg-panel ccg-master-detail-section"><div class="ccg-panel-header"><div><h2>Product 归属</h2><p>每个正式 SKU 只关联一个 Product。</p></div></div><div class="ccg-relation-card"><span>所属 Product</span><strong>${sku.product ? `<a href="/mdm/products/${encodeURIComponent(sku.product.stable_id)}" data-nav="/mdm/products/${encodeURIComponent(sku.product.stable_id)}"><span class="ccg-mono">${esc(sku.product.product_code)}</span> · ${esc(sku.product.product_name)}</a>` : ''}</strong></div></section>
      <section class="ccg-panel ccg-master-detail-section"><div class="ccg-panel-header"><div><h2>系统信息</h2><p>识别码、状态和时间戳为只读字段。</p></div></div><dl class="ccg-definition-grid"><div><dt>系统唯一编号</dt><dd class="ccg-mono">${esc(displayValue(sku.stable_id, ''))}</dd></div><div><dt>状态</dt><dd>${status}</dd></div><div><dt>条码</dt><dd class="ccg-mono">${esc(displayValue(sku.barcode, ''))}</dd></div><div><dt>创建时间</dt><dd class="ccg-tabular">${esc(sku.created_at ? formatTime(sku.created_at) : '')}</dd></div><div><dt>更新时间</dt><dd class="ccg-tabular">${esc(sku.updated_at ? formatTime(sku.updated_at) : '')}</dd></div></dl></section></div>`;
  }

  async function loadSkuDetail(stableId, sequence = state.routeSequence, toast = '') {
    setTopbar('SKU 详情', 'products'); setContent(loadingMarkup());
    try {
      const sku = await mdmFetch(`/skus/${encodeURIComponent(stableId)}`);
      if (sequence !== state.routeSequence) return;
      local.sku = sku; local.editing = false; setContent(skuDetailMarkup(sku)); if (toast) showToast(toast);
    } catch (error) { setContent(`${masterPageHeader('SKU 详情', '查看 SKU 业务字段、Product 归属与系统信息。')}${stateMarkup('error', 'SKU 加载失败', error.message, '返回 SKU 列表', 'back-skus')}`); }
  }

  function skuFormMarkup(sku) {
    if (!canWrite()) return `${masterPageHeader('编辑 SKU', 'SKU 编码与 Product 归属不在普通编辑表单中修改。')}${stateMarkup('access', '只读权限', '当前角色只能查看 SKU；MDM 服务也会拒绝写请求。', '返回 SKU 列表', 'back-skus')}`;
    const inputs = SKU_FIELDS.map((field) => `<label class="ccg-form-field ${field === 'sku_name' ? 'span-2' : ''}"><span class="ccg-form-label ${field === 'sku_name' ? 'required' : ''}">${SKU_LABELS[field]}</span><input class="ccg-input ${field === 'case_pack' ? 'ccg-tabular' : ''}" name="${field}" ${field === 'sku_name' ? 'required' : ''} ${field === 'case_pack' ? 'type="number" min="0.001" step="0.001"' : ''} value="${esc(sku[field] ?? '')}"></label>`).join('');
    return `${masterPageHeader('编辑 SKU', 'SKU 编码与 Product 归属不在普通编辑表单中修改；归属调整使用专用治理动作。')}
      <form id="sku-form" class="ccg-master-form"><section class="ccg-panel ccg-master-form-section"><div class="ccg-panel-header"><div><h2>可编辑业务字段</h2><p>SKU 编码 <span class="ccg-mono">${esc(sku.sku_code)}</span> 为永久识别码，系统唯一编号、状态与时间戳只读。</p></div></div><div class="ccg-form-grid">${inputs}</div><div id="sku-form-error" class="inline-error" hidden></div><div class="ccg-form-actions"><button type="button" class="ccg-button ccg-button-secondary" data-action="sku-cancel-edit">取消</button><button class="ccg-button ccg-button-primary">保存 SKU</button></div></section>
      <aside class="ccg-panel ccg-master-form-summary"><div class="ccg-panel-header"><div><h2>重大字段确认</h2><p>规格、净重或箱规变化会要求二次确认；所有实际变化按字段写入变更日志。</p></div></div><div class="ccg-capability-note"><strong>所属 Product</strong><span>${esc(sku.product ? `${sku.product.product_code} · ${sku.product.product_name}` : '未关联')}</span></div></aside></form>`;
  }

  function skuPayload(form) {
    const data = new FormData(form); const payload = {};
    SKU_FIELDS.forEach((field) => {
      const raw = String(data.get(field) || '').trim();
      payload[field] = field === 'case_pack' ? (raw ? Number(raw) : null) : (raw || null);
    });
    return payload;
  }

  async function patchSku(payload, confirmed = false) {
    return mdmFetch(`/skus/${encodeURIComponent(local.sku.stable_id)}`, { method: 'PATCH', body: JSON.stringify({ ...payload, confirm_significant_change: confirmed }) });
  }

  async function submitSku(form) {
    const payload = skuPayload(form); const significant = SIGNIFICANT_SKU_FIELDS.filter((field) => comparable(payload[field]) !== comparable(local.sku[field]));
    const save = async (confirmed) => {
      try { const saved = await patchSku(payload, confirmed); closeDialog(); local.sku = saved; await loadSkuDetail(saved.stable_id, state.routeSequence, 'SKU 已更新。'); }
      catch (error) { const box = document.getElementById(confirmed ? 'dialog-error' : 'sku-form-error'); box.textContent = error.message; box.hidden = false; }
    };
    if (!significant.length) { await save(false); return; }
    openDialog('确认重大 SKU 字段变化', `以下字段发生变化：${significant.map((field) => SKU_LABELS[field]).join('、')}。\n\n保存后会逐字段记录旧值与新值。`, '确认并保存', 'danger', () => save(true));
  }

  function openDialog(title, copy, label, tone, action, extraHtml = '') {
    const layer = document.getElementById('confirm-layer'); const confirm = document.getElementById('dialog-confirm');
    document.getElementById('dialog-title').textContent = title;
    document.getElementById('dialog-copy').innerHTML = `${extraHtml}<p>${esc(copy)}</p>`;
    document.getElementById('dialog-error').hidden = true; document.getElementById('dialog-icon').textContent = tone === 'danger' ? '!' : '↔';
    const buttonTone = tone === 'danger' ? 'ccg-button-danger' : tone === 'success' ? 'ccg-button-success' : 'ccg-button-primary';
    confirm.textContent = label; confirm.className = `ccg-button ${buttonTone}`; confirm.disabled = false;
    state.dialogAction = action; layer.hidden = false; confirm.focus();
  }

  function lifecycle(kind, action) {
    const item = kind === 'product' ? local.product : local.sku; if (!item || !canWrite()) return;
    const deactivating = action === 'deactivate'; const label = kind === 'product' ? item.product_name : item.sku_name;
    const childWarning = kind === 'product' && deactivating ? `\n\n当前共有 ${item.skus.filter((sku) => sku.status === 'ACTIVE').length} 个有效 SKU。确认后不会级联停用 SKU。` : '';
    openDialog(`${deactivating ? '停用' : '启用'} ${label}？`, `${deactivating ? '停用后不可用于新的主档关系。' : '启用后可恢复用于主档关系。'}${childWarning}`, deactivating ? '确认停用' : '确认启用', deactivating ? 'danger' : 'success', async () => {
      try {
        await mdmFetch(`/${kind === 'product' ? 'products' : 'skus'}/${encodeURIComponent(item.stable_id)}/${action}`, { method: 'POST', body: kind === 'product' && deactivating ? JSON.stringify({ confirm_active_skus: true }) : undefined });
        closeDialog();
        if (kind === 'product') await loadProductDetail(item.stable_id, state.routeSequence, `Product 已${deactivating ? '停用' : '启用'}。`);
        else await loadSkuDetail(item.stable_id, state.routeSequence, `SKU 已${deactivating ? '停用' : '启用'}。`);
      } catch (error) { const box = document.getElementById('dialog-error'); box.textContent = error.message; box.hidden = false; }
    });
  }

  async function reassign(skuStableId) {
    if (!canWrite()) return;
    try {
      const sku = local.sku?.stable_id === skuStableId ? local.sku : await mdmFetch(`/skus/${encodeURIComponent(skuStableId)}`);
      const current = sku.product?.stable_id || '';
      const select = selectorControlMarkup({ prefix: 'reassign-product', kind: 'product', purpose: 'reassign', label: '目标 Product', excludeStableId: current });
      openDialog('调整 SKU 的 Product 归属', `SKU：${sku.sku_code} · ${sku.sku_name}\n当前 Product：${sku.product ? `${sku.product.product_code} · ${sku.product.product_name}` : '未关联'}\n\n这是单一后端治理动作。若原 Product 变为零 SKU，系统只警告且不会自动改变其状态。`, '确认调整归属', 'danger', async () => {
        const target = document.getElementById('reassign-product-id').value;
        if (!target) {
          const box = document.getElementById('dialog-error'); box.textContent = '请先搜索并选择目标 Product。'; box.hidden = false; return;
        }
        try {
          const result = await mdmFetch(`/skus/${encodeURIComponent(sku.stable_id)}/reassign-product`, { method: 'POST', body: JSON.stringify({ product_stable_id: target, confirm: true }) });
          closeDialog(); const message = result.source_product_empty ? '归属已调整；原 Product 已无 SKU，状态保持不变。' : 'SKU Product 归属已调整。';
          if (local.product) await loadProductDetail(local.product.stable_id, state.routeSequence, message); else await loadSkuDetail(sku.stable_id, state.routeSequence, message);
        } catch (error) { const box = document.getElementById('dialog-error'); box.textContent = error.message; box.hidden = false; }
      }, select);
    } catch (error) { showToast(error.message); }
  }

  function applyFilters(kind, form) {
    const data = new FormData(form); const filters = kind === 'product' ? local.productFilters : local.skuFilters;
    filters.search = String(data.get('search') || '').trim(); filters.status = String(data.get('status') || 'ACTIVE'); filters.page = 1;
    if (kind === 'product') { const [sort, order] = String(data.get('sort-order') || 'updated_at:desc').split(':'); filters.sort = sort; filters.order = order; }
    else {
      filters.product_stable_id = String(data.get('product_stable_id') || '');
      filters.sku_stable_id = String(data.get('sku_stable_id') || '');
      Object.entries(SKU_DYNAMIC_FILTERS).forEach(([field, definition]) => {
        filters[field] = definition.type === 'multi'
          ? data.getAll(field).map((value) => String(value)).filter(Boolean)
          : String(data.get(field) || '').trim();
      });
    }
    setUrl(kind, filters, false); kind === 'product' ? renderProducts(state.routeSequence) : renderSkus(state.routeSequence);
  }

  function changePage(kind, page) {
    const pageData = kind === 'product' ? local.productPage : local.skuPage; const filters = kind === 'product' ? local.productFilters : local.skuFilters;
    if (!pageData || page < 1 || page > pageData.pages) return; filters.page = page; setUrl(kind, filters);
    kind === 'product' ? renderProducts(state.routeSequence) : renderSkus(state.routeSequence);
  }

  function selectSelectorOption(target) {
    const { kind, purpose, prefix, stableId, code, name } = target.dataset;
    const input = document.getElementById(`${prefix}-input`);
    const id = document.getElementById(`${prefix}-id`);
    const results = document.getElementById(`${prefix}-results`);
    if (input) { input.value = `${code} · ${name}`; input.setAttribute('aria-expanded', 'false'); }
    if (id) id.value = stableId;
    if (results) results.hidden = true;
    const clear = document.querySelector(`[data-action="selector-clear"][data-prefix="${prefix}"]`);
    if (clear) clear.hidden = false;
    if (purpose !== 'filter') return;
    local.skuFilters.page = 1;
    if (kind === 'product') local.skuFilters.product_stable_id = stableId;
    else {
      local.skuFilters.sku_stable_id = stableId;
      local.skuFilters.search = '';
      const searchValue = document.getElementById(`${prefix}-search-value`);
      if (searchValue) searchValue.value = '';
    }
    setUrl('sku', local.skuFilters, false);
    renderSkus(state.routeSequence);
  }

  function clearSelector(target) {
    const { kind, purpose, prefix } = target.dataset;
    const input = document.getElementById(`${prefix}-input`);
    const id = document.getElementById(`${prefix}-id`);
    const results = document.getElementById(`${prefix}-results`);
    if (input) { input.value = ''; input.setAttribute('aria-expanded', 'false'); }
    if (id) id.value = '';
    if (results) results.hidden = true;
    target.hidden = true;
    if (purpose !== 'filter') return;
    local.skuFilters.page = 1;
    if (kind === 'product') local.skuFilters.product_stable_id = '';
    else {
      local.skuFilters.sku_stable_id = '';
      local.skuFilters.search = '';
      const searchValue = document.getElementById(`${prefix}-search-value`);
      if (searchValue) searchValue.value = '';
    }
    setUrl('sku', local.skuFilters, false);
    renderSkus(state.routeSequence);
  }

  function handleAction(action, target) {
    if (action === 'product-new') navigate('/mdm/products/new');
    else if (action === 'product-import') navigate('/mdm/goods-import-center/upload');
    else if (action === 'sku-import') navigate('/mdm/goods-import-center/upload');
    else if (action === 'back-products') navigate('/mdm/products');
    else if (action === 'back-skus') navigate('/mdm/skus');
    else if (action === 'product-retry') renderProducts(state.routeSequence);
    else if (action === 'sku-retry') renderSkus(state.routeSequence);
    else if (action === 'product-filters-reset') navigate('/mdm/products');
    else if (action === 'sku-filters-reset') navigate('/mdm/skus');
    else if (action === 'sku-filter-add') {
      const field = target.dataset.field;
      if (!SKU_DYNAMIC_FILTERS[field] || local.skuDraftFilterFields.has(field)) return true;
      local.skuDraftFilterFields.add(field);
      setSkuContent(local.skuPage);
      document.querySelector(`[name="${field}"]`)?.focus();
    }
    else if (action === 'sku-filter-remove') {
      const field = target.dataset.field;
      local.skuDraftFilterFields.delete(field);
      local.skuFilters[field] = SKU_DYNAMIC_FILTERS[field]?.type === 'multi' ? [] : '';
      local.skuFilters.page = 1;
      setUrl('sku', local.skuFilters, false);
      renderSkus(state.routeSequence);
    }
    else if (action === 'selector-select') selectSelectorOption(target);
    else if (action === 'selector-clear') clearSelector(target);
    else if (action === 'product-page-prev') changePage('product', local.productPage.page - 1);
    else if (action === 'product-page-next') changePage('product', local.productPage.page + 1);
    else if (action === 'sku-page-prev') changePage('sku', local.skuPage.page - 1);
    else if (action === 'sku-page-next') changePage('sku', local.skuPage.page + 1);
    else if (action === 'product-edit') renderProductForm(local.product);
    else if (action === 'product-cancel-edit') local.product ? loadProductDetail(local.product.stable_id, state.routeSequence) : navigate('/mdm/products');
    else if (action === 'product-lifecycle') lifecycle('product', target.dataset.lifecycle);
    else if (action === 'sku-edit') { local.editing = true; setTopbar('编辑 SKU', 'products'); setContent(skuFormMarkup(local.sku)); }
    else if (action === 'sku-cancel-edit') loadSkuDetail(local.sku.stable_id, state.routeSequence);
    else if (action === 'sku-lifecycle') lifecycle('sku', target.dataset.lifecycle);
    else if (action === 'sku-reassign') reassign(target.dataset.sku);
    else return false;
    return true;
  }

  function handleSubmit(event) {
    if (event.target.id === 'product-filter-form') { event.preventDefault(); applyFilters('product', event.target); return true; }
    if (event.target.id === 'sku-filter-form') { event.preventDefault(); applyFilters('sku', event.target); return true; }
    if (event.target.id === 'product-form') { event.preventDefault(); submitProduct(event.target); return true; }
    if (event.target.id === 'sku-form') { event.preventDefault(); submitSku(event.target); return true; }
    return false;
  }

  function handleChange(event) {
    if (event.target.dataset.action === 'sku-column-toggle') {
      const next = new Set(local.skuVisibleColumns || loadSkuVisibleColumns());
      if (event.target.checked) next.add(event.target.value); else next.delete(event.target.value);
      if (!next.size) { event.target.checked = true; return true; }
      local.skuVisibleColumns = next; saveSkuVisibleColumns(next);
      document.querySelectorAll('[data-sku-column]').forEach((cell) => { cell.hidden = !next.has(cell.dataset.skuColumn); });
      const count = document.getElementById('sku-visible-field-count'); if (count) count.textContent = `${next.size}/${SKU_LIST_COLUMNS.length}`;
      const empty = document.getElementById('sku-empty-cell'); if (empty) empty.colSpan = next.size;
      return true;
    }
    if (event.target.dataset.action === 'product-page-size' || event.target.dataset.action === 'sku-page-size') {
      const kind = event.target.dataset.action.startsWith('product') ? 'product' : 'sku'; const filters = kind === 'product' ? local.productFilters : local.skuFilters;
      filters.page_size = Number(event.target.value); filters.page = 1; setUrl(kind, filters); kind === 'product' ? renderProducts(state.routeSequence) : renderSkus(state.routeSequence); return true;
    }
    return false;
  }

  function handleInput(event) {
    if (!event.target.dataset.selectorKind) return false;
    scheduleSelectorSearch(event.target);
    return true;
  }

  return {
    renderProducts, loadProductDetail, renderProductForm, renderSkus, loadSkuDetail,
    handleAction, handleSubmit, handleChange, handleInput,
    test: {
      paramsFrom, queryString, productListMarkup, productDetailMarkup, skuListMarkup,
      skuDetailMarkup, productFormMarkup, skuFormMarkup, loadSkuVisibleColumns,
      saveSkuVisibleColumns, SKU_LIST_COLUMNS, DEFAULT_SKU_LIST_COLUMNS,
      SKU_DYNAMIC_FILTERS, SELECTOR_DEBOUNCE_MS, SELECTOR_MIN_CHARS, SELECTOR_LIMIT,
      searchProducts, searchSkus, selectorResultsMarkup,
    },
  };
})();
