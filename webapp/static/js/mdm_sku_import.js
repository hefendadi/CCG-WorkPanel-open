/* SKU Import V1 workspace; business decisions remain in the backend. */
(function () {
  const BATCHES_PATH = '/sku-import/batches';
  const local = { contract: null, review: null, activeTab: null, result: null, uploading: false };

  function contract() {
    if (local.contract) return local.contract;
    const node = document.getElementById('sku-import-ui-contract');
    try { local.contract = JSON.parse(node ? node.textContent : '{}'); } catch (_error) { local.contract = {}; }
    return local.contract;
  }

  function routeFromPath(pathname) {
    const path = String(pathname || '').replace(/\/+$/, '') || '/';
    if (path === '/mdm/sku-import-center') return { name: 'sku-import-home', section: 'sku-import-center' };
    if (path === '/mdm/sku-import-center/upload') return { name: 'sku-import-upload', section: 'sku-import-center' };
    const match = path.match(/^\/mdm\/sku-import-center\/batches\/([^/]+)\/(review|commit)$/);
    if (!match) return null;
    return { name: `sku-import-${match[2]}`, section: 'sku-import-center', batchId: decodeURIComponent(match[1]) };
  }

  function pathFor(batchId, page) {
    return `/mdm/sku-import-center/batches/${encodeURIComponent(batchId)}/${page}`;
  }

  function badge(status, row = false) {
    const copy = row ? contract().row_status : contract().batch_status;
    const tone = ['COMMITTED', 'READY', 'READY_TO_COMMIT'].includes(status) ? 'success'
      : ['ERROR', 'FAILED'].includes(status) ? 'danger' : ['WARNING', 'REVIEW'].includes(status) ? 'warning' : 'neutral';
    return `<span class="ccg-status ccg-status-${tone} ccg-import-status">${esc((copy || {})[status] || '未知状态')}</span>`;
  }

  function counts(value) {
    const items = [['总行数', 'total', 'neutral'], ['需要修改', 'error', 'danger'], ['需要确认', 'warning', 'warning'], ['已存在', 'existing', 'neutral'], ['可新增', 'ready', 'success']];
    return `<div class="ccg-import-counts import-counts">${items.map(([label, key, tone]) => `<div class="ccg-import-count ccg-import-count-${tone} import-count import-count-${tone}"><span>${label}</span><strong>${Number(value[key] || 0)}</strong></div>`).join('')}</div>`;
  }

  function homeMarkup(payload) {
    const items = Array.isArray(payload.items) ? payload.items : [];
    const action = canWrite() ? '<button class="ccg-button ccg-button-primary" data-action="sku-import-new">+ 新建 SKU 导入</button>' : '';
    if (!items.length) return `${importPageHeader('SKU 导入', '从 Excel 批量新增 SKU 主数据。', action)}${stateMarkup('empty', '还没有 SKU 导入批次', '上传 .xlsx 文件后，可在这里查看进度。', canWrite() ? '新建 SKU 导入' : '', canWrite() ? 'sku-import-new' : '')}`;
    const rows = items.map((batch) => {
      const page = ['READY_TO_COMMIT', 'COMMITTED'].includes(batch.status) ? 'commit' : 'review';
      return `<tr><td><strong>${esc(batch.source_file)}</strong><span class="cell-secondary">批次 ${esc(batch.batch_id)}</span></td><td>${badge(batch.status)}</td><td>${esc(formatTime(batch.created_at))}</td><td>${Number(batch.counts.total || 0)}</td><td>${Number(batch.counts.error || 0)}</td><td>${Number(batch.counts.warning || 0)}</td><td><a class="ccg-button ccg-button-secondary" data-nav="${pathFor(batch.batch_id, page)}">查看批次</a></td></tr>`;
    }).join('');
    return `${importPageHeader('SKU 导入', '跟踪 SKU Excel 的检查、确认与提交进度。', action)}<section class="ccg-panel"><div class="ccg-panel-header"><h2>批次列表</h2></div>${importTableToolsMarkup()}<div class="ccg-table-scroll table-wrap"><table class="ccg-table ccg-import-table import-table"><thead><tr><th>文件</th><th>状态</th><th>上传时间</th><th>总行数</th><th>需要修改</th><th>需要确认</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function uploadMarkup(message = '') {
    return `${importPageHeader('新建 SKU 导入', '模板字段：SKU Code、SKU 名称、所属 Product Code（其余字段选填）。', '<button class="ccg-button ccg-button-secondary" data-action="sku-import-download-template">下载导入模板</button><button class="ccg-button ccg-button-secondary" data-action="sku-import-home">返回批次列表</button>')}${message ? `<div class="banner banner-warning">${esc(message)}</div>` : ''}<section class="ccg-panel ccg-import-upload-panel import-upload-panel"><form id="sku-import-upload-form"><label class="ccg-import-dropzone import-dropzone" for="sku-import-file"><span class="ccg-import-upload-icon import-upload-icon">↑</span><strong>选择 SKU Excel 文件</strong><small id="sku-import-file-name">仅支持 .xlsx；所属 Product 必须已提交到主数据</small></label><input id="sku-import-file" name="file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required><div id="sku-import-progress" class="ccg-import-upload-progress import-upload-progress" role="status" aria-live="polite" hidden><span class="import-processing-spinner"></span><span><strong>正在上传并检查…</strong><small>正在创建 SKU 导入批次，请勿重复提交。</small></span></div><div id="sku-import-error" class="banner banner-warning" role="alert" hidden></div><div class="import-upload-actions"><button id="sku-import-submit" class="ccg-button ccg-button-primary" type="submit">上传并检查</button></div></form></section>`;
  }

  function identity(row) {
    return `<div class="ccg-import-row-identity import-row-identity"><strong>${esc(row.identity.sku_name || '未填写名称')}</strong><span>${esc(row.identity.sku_code || '未填写 Code')} · Excel 第 ${Number(row.row_number)} 行</span></div>`;
  }

  function tabsFor(payload) {
    const result = Object.fromEntries(Object.keys(contract().tab_labels || {}).map((name) => [name, []]));
    (payload.rows || []).forEach((row) => { const tab = (contract().tab_status || {})[row.status]; if (tab && result[tab]) result[tab].push(row); });
    return result;
  }

  function rowList(name, rows, payload) {
    if (!rows.length) return '<div class="import-tab-empty">该分类暂无数据</div>';
    const canAck = canWrite() && Number(payload.counts.error || 0) === 0 && (contract().reviewable_statuses || []).includes(payload.status);
    return `<div class="ccg-import-row-list import-row-list">${rows.map((row) => {
      const messages = (row.findings || []).map((item) => (contract().issue_copy || {})[item.code]).filter(Boolean);
      const list = messages.length ? `<ul class="import-message-list">${messages.map((message) => `<li>${esc(message)}</li>`).join('')}</ul>` : '';
      let detail = '<p class="ccg-import-readonly-note import-readonly-note">该行仅供查看，不会更新已有 SKU。</p>';
      if (name === 'needs_fix') detail = `${list}<p class="ccg-import-readonly-note import-readonly-note">请在 Excel 中修正后重新上传。</p>`;
      if (name === 'needs_confirm') {
        const ids = (row.findings || []).filter((item) => item.severity === 'WARNING' && item.finding_id).map((item) => item.finding_id);
        detail = `${list}${canAck ? `<button class="ccg-button ccg-button-primary" data-action="sku-import-ack" data-ids="${ids.join(',')}">确认无误，继续导入</button>` : '<p class="ccg-import-readonly-note import-readonly-note">存在需要修改的行，暂不能确认。</p>'}`;
      }
      if (name === 'existing' && row.existing && row.existing.matched_stable_id) detail = `${list}<p class="ccg-import-readonly-note import-readonly-note">已有 SKU 保持不变。</p><a class="ccg-button ccg-button-secondary" data-nav="/mdm/skus/${encodeURIComponent(row.existing.matched_stable_id)}">查看已有 SKU</a>`;
      return `<article class="ccg-import-row-card import-row-card">${identity(row)}<div class="import-row-detail">${detail}</div></article>`;
    }).join('')}</div>`;
  }

  function reviewMarkup(payload, active = null, banner = '') {
    const tabs = tabsFor(payload); const names = Object.keys(contract().tab_labels || {});
    const preferred = Number(payload.counts.error || 0) ? 'needs_fix' : Number(payload.counts.warning || 0) ? 'needs_confirm' : Number(payload.counts.existing || 0) ? 'existing' : 'create';
    const selected = names.includes(active) ? active : preferred; local.activeTab = selected;
    const buttons = names.map((name) => `<button class="ccg-local-tab ccg-import-review-tab import-tab ${name === selected ? 'active' : ''}" type="button" role="tab" aria-selected="${name === selected}" data-action="sku-import-tab" data-tab="${name}">${esc(contract().tab_labels[name])}<span>${tabs[name].length}</span></button>`).join('');
    const next = payload.status === 'READY_TO_COMMIT' ? `<a class="ccg-button ccg-button-primary" data-nav="${pathFor(payload.batch_id, 'commit')}">查看提交汇总</a>` : '';
    const blocked = Number(payload.counts.error || 0) ? `<div class="banner banner-warning"><strong>请先处理需要修改的行</strong><br>${esc(contract().copy.error_first_blocked)}</div>` : '';
    return `${importPageHeader('SKU 批次处理', '先修改错误；已存在行只读，可新增行由具备 MDM EDIT 权限的用户提交。', next)}${banner ? `<div class="banner banner-success">${esc(banner)}</div>` : ''}${blocked}<section class="ccg-panel ccg-import-summary import-summary"><div class="ccg-panel-header"><div><h2>${esc(payload.source_file)}</h2><p>批次 ${esc(payload.batch_id)}</p></div>${badge(payload.status)}</div><div class="ccg-panel-body">${counts(payload.counts)}</div></section><section class="ccg-panel ccg-import-review-panel import-review-panel">${importReviewToolsMarkup()}<div class="ccg-local-tabs ccg-import-review-tabs import-tabs" role="tablist">${buttons}</div><div class="ccg-import-tab-panel import-tab-panel" role="tabpanel">${rowList(selected, tabs[selected], payload)}</div></section>`;
  }

  function commitMarkup(payload) {
    const created = (payload.rows || []).filter((row) => row.status === 'COMMITTED');
    const existing = (payload.rows || []).filter((row) => row.status === 'EXISTING');
    if (payload.status === 'COMMITTED') {
      const summary = (local.result && local.result.result_summary) || { created: created.length, existing: existing.length, total: created.length + existing.length };
      return `${importPageHeader('SKU 提交结果', '本批次已完成，结果仅供查看。', '<button class="ccg-button ccg-button-primary" data-action="sku-import-home">返回批次列表</button>')}<div class="banner banner-success"><strong>已创建 ${Number(summary.created || 0)} 个 SKU，${Number(summary.existing || 0)} 个已存在 SKU 保持不变</strong></div><section class="ccg-panel ccg-import-commit-card import-commit-card"><div class="ccg-panel-body">${counts({ total: summary.total, ready: summary.created, existing: summary.existing, error: 0, warning: 0 })}<p class="ccg-import-readonly-note import-readonly-note">该批次已提交，不能再次确认或提交。</p></div></section>`;
    }
    const admin = canWrite(); const ready = payload.status === 'READY_TO_COMMIT';
    const message = !admin ? contract().copy.commit_forbidden : !ready ? contract().copy.commit_not_ready : '提交将一次性创建新 SKU，已存在 SKU 不会被修改。';
    const action = admin && ready ? '<button class="ccg-button ccg-button-primary" data-action="sku-import-open-commit">确认提交 SKU</button>' : '';
    return `${importPageHeader('提交 SKU 批次', '提交前请最后确认批次汇总。', '<button class="ccg-button ccg-button-secondary" data-action="sku-import-back-review">返回批次处理</button>')}<section class="ccg-panel ccg-import-commit-card import-commit-card"><div class="ccg-panel-header"><h2>${esc(payload.source_file)}</h2>${badge(payload.status)}</div><div class="ccg-panel-body">${counts(payload.counts)}<div class="banner ${admin && ready ? 'banner-info' : 'banner-warning'}">${esc(message)}</div><div class="import-result-actions">${action}</div></div></section>`;
  }

  async function load(batchId, sequence, page, banner = '') {
    setTopbar(page === 'commit' ? '提交 SKU 批次' : 'SKU 批次处理', 'products'); setContent(`${importPageHeader(page === 'commit' ? '提交 SKU 批次' : 'SKU 批次处理', '正在读取导入批次。')}${loadingMarkup()}`);
    try { const payload = await mdmFetch(`${BATCHES_PATH}/${encodeURIComponent(batchId)}/review`); if (sequence !== state.routeSequence) return; local.review = payload; setContent(page === 'commit' ? commitMarkup(payload) : reviewMarkup(payload, null, banner)); }
    catch (error) { if (sequence === state.routeSequence) setContent(`${importPageHeader(page === 'commit' ? '提交 SKU 批次' : 'SKU 批次处理', 'SKU 导入批次。')}${stateMarkup('error', error.status === 404 ? contract().copy.review_not_found : contract().copy.review_load_error, contract().copy.review_load_error, '重试', 'retry-route')}`); }
  }

  async function render(current, sequence) {
    if (current.name === 'sku-import-home') { setTopbar('SKU 导入', 'products'); setContent(`${importPageHeader('SKU 导入', '正在读取导入批次。')}${loadingMarkup('table')}`); try { const data = await mdmFetch(`${BATCHES_PATH}?page=1&page_size=50`); if (sequence === state.routeSequence) setContent(homeMarkup(data)); } catch (_error) { if (sequence === state.routeSequence) setContent(`${importPageHeader('SKU 导入', '跟踪 SKU Excel 的检查、确认与提交进度。')}${stateMarkup('error', '批次列表加载失败', '请稍后重试', '重试', 'retry-route')}`); } return; }
    if (current.name === 'sku-import-upload') { local.uploading = false; setTopbar('新建 SKU 导入', 'products'); setContent(canWrite() ? uploadMarkup() : `${importPageHeader('新建 SKU 导入', '上传并校验 SKU Excel。')}${stateMarkup('error', '当前为只读权限', '需要 MDM EDIT 才能上传文件。', '返回批次列表', 'sku-import-home')}`); return; }
    return load(current.batchId, sequence, current.name === 'sku-import-commit' ? 'commit' : 'review');
  }

  function uploadState(form, busy, error = '') {
    const input = form.querySelector('input[type="file"]'); const button = document.getElementById('sku-import-submit'); const progress = document.getElementById('sku-import-progress'); const box = document.getElementById('sku-import-error');
    if (busy) form.setAttribute('aria-busy', 'true'); else form.removeAttribute('aria-busy'); if (input) input.disabled = busy;
    if (button) { button.disabled = busy; button.textContent = busy ? '正在上传并检查…' : '上传并检查'; } if (progress) progress.hidden = !busy;
    if (box) { box.textContent = error; box.hidden = !error; }
  }

  async function submitUpload(form) {
    if (local.uploading) return; const input = form.querySelector('input[type="file"]'); const file = input && input.files && input.files[0];
    if (!file || !file.name.toLowerCase().endsWith('.xlsx')) { setContent(uploadMarkup(contract().copy.upload_not_xlsx)); return; }
    local.uploading = true; uploadState(form, true); const body = new FormData(); body.append('file', file);
    try { const result = await mdmFetch(BATCHES_PATH, { method: 'POST', body }); local.uploading = false; if (result.batch_id) navigate(pathFor(result.batch_id, 'review')); else { const message = (((result.validation || {}).file_errors || [])[0] || {}).message || contract().copy.upload_file_rejected; setContent(uploadMarkup(message)); } }
    catch (_error) { local.uploading = false; uploadState(form, false, contract().copy.upload_server_error); }
  }

  async function acknowledge(target) {
    if (!local.review || Number(local.review.counts.error || 0)) return; const ids = String(target.dataset.ids || '').split(',').filter(Boolean).map(Number); if (!ids.length) return;
    target.disabled = true; target.textContent = '确认中…';
    try { await mdmFetch(`${BATCHES_PATH}/${encodeURIComponent(local.review.batch_id)}/acknowledge`, { method: 'POST', body: JSON.stringify({ review_version: local.review.review_version, finding_ids: ids }) }); await load(local.review.batch_id, state.routeSequence, 'review', contract().copy.acknowledge_success); }
    catch (error) { target.disabled = false; target.textContent = '确认无误，继续导入'; showToast(error.code === 'MDM_IMPORT_STALE_REVIEW_VERSION' ? contract().copy.acknowledge_stale : contract().copy.acknowledge_error); }
  }

  function openCommit() {
    if (!local.review) return; const layer = document.getElementById('confirm-layer'); const confirm = document.getElementById('dialog-confirm');
    document.getElementById('dialog-title').textContent = '确认提交 SKU 批次？'; document.getElementById('dialog-copy').textContent = `文件：${local.review.source_file}\n将新增 ${Number(local.review.counts.ready || 0)} 个 SKU，${Number(local.review.counts.existing || 0)} 个已存在 SKU 保持不变。`; document.getElementById('dialog-error').hidden = true;
    confirm.textContent = '确认提交'; confirm.className = 'ccg-button ccg-button-primary'; confirm.disabled = false;
    state.dialogAction = async () => { confirm.disabled = true; confirm.textContent = '提交中…'; try { local.result = await mdmFetch(`${BATCHES_PATH}/${encodeURIComponent(local.review.batch_id)}/commit`, { method: 'POST' }); closeDialog(); await load(local.review.batch_id, state.routeSequence, 'commit'); } catch (_error) { const box = document.getElementById('dialog-error'); box.textContent = contract().copy.commit_server_error; box.hidden = false; confirm.disabled = false; confirm.textContent = '确认提交'; } };
    layer.hidden = false; confirm.focus();
  }

  function handleAction(action, target) {
    if (action === 'sku-import' || action === 'sku-import-home') navigate('/mdm/sku-import-center');
    else if (action === 'sku-import-new') navigate('/mdm/sku-import-center/upload');
    else if (action === 'sku-import-download-template') downloadMdmTemplate('/sku-import/template', 'sku_import_v1_template.xlsx').catch(() => showToast('模板下载失败，请稍后重试'));
    else if (action === 'sku-import-tab' && local.review) setContent(reviewMarkup(local.review, target.dataset.tab));
    else if (action === 'sku-import-ack') acknowledge(target);
    else if (action === 'sku-import-open-commit') openCommit();
    else if (action === 'sku-import-back-review' && local.review) navigate(pathFor(local.review.batch_id, 'review'));
    else return false; return true;
  }

  function handleSubmit(event) { if (event.target.id !== 'sku-import-upload-form') return false; event.preventDefault(); submitUpload(event.target); return true; }
  function handleChange(event) { if (event.target.id !== 'sku-import-file') return false; const file = event.target.files && event.target.files[0]; const label = document.getElementById('sku-import-file-name'); if (label) label.textContent = file ? file.name : '仅支持 .xlsx'; return true; }

  globalThis.MDMSKUImport = { routeFromPath, render, handleAction, handleSubmit, handleChange };
  globalThis.MDMSKUImportTest = { configure: (value) => { local.contract = value; }, routeFromPath, homeMarkup, uploadMarkup, reviewMarkup, commitMarkup, tabsFor, submitUpload, uploadState };
}());
