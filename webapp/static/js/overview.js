/** Read-only Overview: existing API summaries and bounded workflow lists. */
(async function initOverview() {
  'use strict';
  await globalThis.CCGShellReady;
  const user = currentUser;
  if (!user) return;
  const el = id => document.getElementById(id);
  const salesBase = '/api/v1/sales/actual';
  const mdmBase = '/api/v1/mdm';
  const canMDM = hasPermission('mdm', 'VIEW', user);
  const canSales = hasPermission('sales_actual', 'VIEW', user);
  const sources = [
    { name: '客户', path: '/customer-import/batches', page: '/mdm/import-center' },
    { name: '商品', path: '/product-import/batches', page: '/mdm/product-import-center' },
    { name: 'SKU', path: '/sku-import/batches', page: '/mdm/sku-import-center' },
    { name: '商品与 SKU', path: '/goods-import/batches', page: '/mdm/goods-import-center' },
  ];
  const mdmPending = {
    UPLOADED: '文件已上传，请查看检查结果',
    READY_FOR_REVIEW: '导入数据等待审核',
    REVIEW: '导入数据需要核对或修改',
    READY_TO_COMMIT: '导入数据已就绪，等待提交',
    FAILED: '导入失败，请查看原因',
  };
  const salesPending = {
    UPLOADED: '文件已上传，请查看处理结果',
    VALIDATED: '销售数据已检查，请查看处理结果',
    PREVIEW_READY: '销售数据等待核对并发布',
    FAILED: '销售数据导入失败，请查看原因',
  };
  async function request(url) {
    const response = await authFetch(url);
    if (!response.ok) throw new Error('数据加载失败，请刷新重试');
    return response.json();
  }
  function empty(message, detail = '') {
    return `<div class="overview-empty">${esc(message)}${detail ? `<p>${esc(detail)}</p>` : ''}</div>`;
  }
  function attentionItem(name, item, description, href, editable) {
    return `<div class="attention-item"><div class="attention-copy"><span class="attention-tag">${esc(name)}</span>${esc(description)}<small>${esc(item.source_file || item.filename || '')} · 批次 ${esc(item.batch_id)}</small></div><a class="ccg-button ccg-button-secondary" href="${esc(href)}">${editable ? '去处理' : '查看'} →</a></div>`;
  }
  async function loadImports() {
    const tasks = [];
    if (canMDM) for (const source of sources) {
      tasks.push({ source, promise: request(`${mdmBase}${source.path}?page=1&page_size=5`) });
    }
    if (canSales) tasks.push({ source: null, promise: request(`${salesBase}/import-batches?offset=0&limit=5`) });
    const results = await Promise.allSettled(tasks.map(task => task.promise));
    const attention = [], imports = [], unavailable = [];
    results.forEach((result, index) => {
      const source = tasks[index].source;
      if (result.status === 'rejected') {
        unavailable.push(source ? `${source.name}导入` : '销售实绩导入');
        if (source) imports.push(`<p>${esc(source.name)}：加载失败</p>`);
        return;
      }
      const data = result.value;
      if (source) {
        imports.push(`<p>${esc(source.name)}：${qty(data.total)} 个批次</p>`);
        for (const item of data.items) {
          const description = mdmPending[item.status];
          if (!description) continue;
          const page = item.status === 'READY_TO_COMMIT' ? 'commit' : 'review';
          attention.push(attentionItem(`${source.name}导入`, item, description,
            `${source.page}/batches/${encodeURIComponent(item.batch_id)}/${page}`, hasPermission('mdm', 'EDIT', user)));
        }
      } else {
        for (const item of data.items) {
          const description = salesPending[item.status];
          if (!description) continue;
          const href = item.status === 'PREVIEW_READY'
            ? `/sales/actual/import-batches/${encodeURIComponent(item.batch_id)}/preview`
            : hasPermission('sales_actual', 'EDIT', user) ? '/sales/actual/import' : '/sales/actual';
          attention.push(attentionItem('销售实绩', item, description, href, hasPermission('sales_actual', 'EDIT', user)));
        }
      }
    });
    el('import-status').innerHTML = canMDM
      ? `<strong>导入批次记录</strong>${imports.join('')}`
      : '<p>暂无主数据查看权限</p>';
    const failure = unavailable.length
      ? `<div class="overview-empty overview-error">${esc(unavailable.join('、'))}加载失败，请刷新重试。<p>待处理事项尚未完整加载。</p></div>` : '';
    el('attention').innerHTML = failure + (attention.length ? attention.join('') : unavailable.length ? ''
      : empty(tasks.length ? '当前范围内暂无待处理事项' : '暂无可查看的业务事项', '仅展示有权限查看的流程；各流程最近 5 个批次。'));
  }
  async function loadMDM() {
    if (!canMDM) {
      el('mdm-status').innerHTML = '<p>暂无主数据查看权限</p>';
      return;
    }
    try {
      const resources = [['customers', '客户'], ['products', '商品'], ['skus', 'SKU'], ['channels', '渠道']];
      const counts = await Promise.all(resources.map(([path]) => request(`${mdmBase}/${path}?page=1&page_size=1&status=ACTIVE`)));
      el('mdm-status').innerHTML = `<strong>当前启用记录</strong><p>${counts.map((data, index) => `${qty(data.total)} ${resources[index][1]}`).join(' · ')}</p>`;
    } catch (_error) {
      el('mdm-status').innerHTML = '<p class="overview-error">主数据加载失败，请刷新重试</p>';
    }
  }
  function table(items, channel = false) {
    return `<thead><tr>${channel ? '<th>渠道</th>' : ''}<th>商品</th>${channel ? '' : '<th class="number">SKU 数</th>'}<th class="number">数量 (Pcs)</th></tr></thead><tbody>` +
      (items.length ? items.map(item => `<tr>${channel ? `<td>${esc(item.channel_name || '未归属')}</td>` : ''}<td>${esc(item.product_name)}</td>${channel ? '' : `<td class="number">${qty(item.sku_count)}</td>`}<td class="number">${qty(item.qty)}</td></tr>`).join('')
        : '<tr><td colspan="3" class="overview-empty">暂无销售记录</td></tr>') + '</tbody>';
  }
  async function loadSales() {
    el('sales-content').hidden = true;
    el('sales-state').hidden = false;
    el('sales-report-link').hidden = !canSales;
    if (!canSales) {
      el('sales-status').innerHTML = '<p>暂无销售实绩查看权限</p>';
      el('sales-state').innerHTML = empty('暂无销售实绩查看权限');
      return;
    }
    try {
      const context = await request(`${salesBase}/dashboard/context`);
      if (!context.current_batch) {
        el('sales-status').innerHTML = '<strong>暂无已发布快照</strong>';
        el('sales-state').innerHTML = empty('暂无已发布销售实绩', '发布销售数据后，可在此查看当前月份的数量快照。');
        return;
      }
      const batch = context.current_batch;
      const query = new URLSearchParams({ batch_id: batch.batch_id });
      const [summary, products, channels] = await Promise.all([
        request(`${salesBase}/dashboard/summary?${query}`),
        request(`${salesBase}/dashboard/products?${query}&page=1&limit=5&sort=-qty`),
        request(`${salesBase}/dashboard/channel-products?${query}&page=1&limit=5&sort=-qty`),
      ]);
      el('sales-status').innerHTML = `<strong>${esc(context.snapshot_month.slice(0, 7))} · 已发布</strong><p>数据截至 ${esc(context.data_end_date || '—')}</p><p>发布时间：${esc(batch.published_at ? batch.published_at.replace('T', ' ') : '—')}</p><p>批次 ${esc(batch.batch_id)}</p>`;
      el('sales-qty').textContent = qty(summary.mtd_qty);
      el('sales-month').textContent = context.snapshot_month.slice(0, 7);
      el('sales-date').textContent = context.data_end_date || '—';
      el('sales-entities').textContent = `${qty(summary.product_count)} / ${qty(summary.sku_count)}`;
      el('overview-products').innerHTML = table(products.items);
      el('overview-channels').innerHTML = table(channels.items, true);
      el('sales-content').hidden = false;
      el('sales-state').hidden = true;
    } catch (_error) {
      el('sales-status').innerHTML = '<p class="overview-error">销售快照加载失败，请刷新重试</p>';
      el('sales-state').innerHTML = empty('销售实绩加载失败', '当前数量展示已停止，请刷新重试。');
    }
  }
  async function refresh() {
    const button = el('overview-refresh');
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = '正在刷新…';
    for (const id of ['sales-status', 'mdm-status', 'import-status']) el(id).textContent = '正在加载…';
    el('attention').innerHTML = empty('正在加载待处理事项…');
    el('sales-state').innerHTML = empty('正在加载已发布销售实绩…');
    try { await Promise.all([loadMDM(), loadImports(), loadSales()]); }
    finally { button.disabled = false; button.textContent = '刷新状态'; }
  }
  el('portal').hidden = false;
  el('overview-refresh').addEventListener('click', refresh);
  await refresh();
}());
