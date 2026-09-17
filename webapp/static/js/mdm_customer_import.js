/* Customer Import V1 workspace. UI vocabulary is injected from ui_contract.py. */
(function () {
  const BATCHES_PATH = '/customer-import/batches';
  const FIELD_COPY = {
    customer_code: '客户编码', customer_name: '客户名称', organization: '组织',
    department: '部门', business_type: '业务类型', market_type: '市场类型',
    format_type: '业态', channel_detail: '渠道明细', is_direct: '是否直营',
    source_created_ym: '创建年月', channel_stable_id: '渠道',
    salesrep_stable_id: '销售员', region_stable_id: '销售地区',
    province_stable_id: '省份', parent_customer_stable_id: '上级客户',
  };

  const importState = {
    contract: null,
    review: null,
    activeTab: null,
    commitResult: null,
    uploading: false,
  };
  const UPLOAD_COPY_FALLBACK = {
    upload_file_rejected: '文件无法解析，请检查后重新上传',
    upload_server_error: '上传失败，请稍后重试',
    upload_not_xlsx: '请选择 .xlsx 格式的导入文件',
  };

  function contract() {
    if (importState.contract) return importState.contract;
    const node = typeof document !== 'undefined' && document.getElementById('customer-import-ui-contract');
    try { importState.contract = JSON.parse(node ? node.textContent : '{}'); } catch (_error) { importState.contract = {}; }
    return importState.contract;
  }

  function uploadCopy(source, key) {
    const copy = source && source.copy;
    return copy && typeof copy[key] === 'string' && copy[key]
      ? copy[key] : UPLOAD_COPY_FALLBACK[key];
  }

  function routeFromPath(pathname) {
    const path = String(pathname || '').replace(/\/+$/, '') || '/';
    if (path === '/mdm/import-center') return { name: 'import-home', section: 'import-center' };
    if (path === '/mdm/import-center/upload') return { name: 'import-upload', section: 'import-center' };
    const match = path.match(/^\/mdm\/import-center\/batches\/([^/]+)\/(review|commit)$/);
    if (!match) return null;
    return {
      name: match[2] === 'review' ? 'import-review' : 'import-commit',
      section: 'import-center', batchId: decodeURIComponent(match[1]),
    };
  }

  function importPath(batchId, page) {
    return `/mdm/import-center/batches/${encodeURIComponent(batchId)}/${page}`;
  }

  function statusBadge(status, kind = 'batch') {
    const c = contract();
    const copy = kind === 'row' ? c.row_status : c.batch_status;
    const tone = status === 'COMMITTED' || status === 'READY' || status === 'READY_TO_COMMIT'
      ? 'success' : status === 'ERROR' || status === 'FAILED' ? 'danger' : status === 'WARNING' || status === 'REVIEW' ? 'warning' : 'neutral';
    return `<span class="ccg-status ccg-status-${tone} ccg-import-status">${esc((copy || {})[status] || '未知状态')}</span>`;
  }

  function countCards(counts) {
    const items = [
      ['总行数', counts.total, 'neutral'], ['需要修改', counts.error, 'danger'],
      ['需要确认', counts.warning, 'warning'], ['已存在', counts.existing, 'neutral'],
      ['可新增', counts.ready, 'success'],
    ];
    return `<div class="ccg-import-counts import-counts">${items.map(([label, value, tone]) => `<div class="ccg-import-count ccg-import-count-${tone} import-count import-count-${tone}"><span>${label}</span><strong>${Number(value || 0)}</strong></div>`).join('')}</div>`;
  }

  function homeMarkup(payload) {
    const c = contract();
    const items = Array.isArray(payload.items) ? payload.items : [];
    const header = importPageHeader('数据导入', '选择主数据类型，使用对应固定模板完成批量导入。');
    if (!items.length) {
      return `${header}<section class="ccg-panel"><div class="ccg-panel-header"><div><h2>客户导入批次</h2><p>最近的客户导入记录</p></div></div>
        ${stateMarkup('empty', '还没有客户导入批次', '上传 .xlsx 文件后，可在这里查看处理进度。', canWrite() ? '新建客户导入' : '', canWrite() ? 'import-new' : '')}</section>`;
    }
    const rows = items.map((batch) => {
      const label = (c.batch_action || {})[batch.status] || '查看批次';
      const page = ['READY_TO_COMMIT', 'COMMITTED'].includes(batch.status) ? 'commit' : 'review';
      return `<tr><td><span class="cell-primary">${esc(batch.source_file)}</span><span class="cell-secondary">批次 ${esc(batch.batch_id)}</span></td>
        <td>${statusBadge(batch.status)}</td><td>${esc(formatTime(batch.created_at))}</td>
        <td><strong>${Number(batch.counts.total || 0)}</strong></td><td class="import-error-number">${Number(batch.counts.error || 0)}</td>
        <td>${Number(batch.counts.warning || 0)}</td><td><a class="ccg-button ccg-button-secondary" href="${importPath(batch.batch_id, page)}" data-nav="${importPath(batch.batch_id, page)}">${esc(label)}</a></td></tr>`;
    }).join('');
    return `${header}
      <section class="ccg-panel"><div class="ccg-panel-header"><div><h2>客户导入批次</h2><p>跟踪客户 Excel 的校验、确认与提交进度</p></div>${canWrite() ? '<button class="ccg-button ccg-button-secondary" type="button" data-action="import-new">新建客户导入</button>' : ''}</div>
      ${importTableToolsMarkup()}<div class="ccg-table-scroll table-wrap"><table class="ccg-table ccg-import-table import-table"><thead><tr><th>文件</th><th>状态</th><th>上传时间</th><th>总行数</th><th>需要修改</th><th>需要确认</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function uploadFormMarkup(message = '', tone = '') {
    const banner = message ? `<div class="banner banner-${tone === 'error' ? 'warning' : tone || 'info'}">${esc(message)}</div>` : '';
    return `${importPageHeader('新建客户导入', '选择符合固定模板的 .xlsx 工作簿。', '<button class="ccg-button ccg-button-secondary" type="button" data-action="import-download-template">下载导入模板</button><button class="ccg-button ccg-button-secondary" type="button" data-action="import-home">返回批次列表</button>', '<a href="/mdm/import-center" data-nav="/mdm/import-center">数据导入</a> / 新建导入')}${banner}
      <section class="ccg-panel ccg-import-upload-panel import-upload-panel"><form id="customer-import-upload-form">
        <label class="ccg-import-dropzone import-dropzone" for="customer-import-file"><span class="ccg-import-upload-icon import-upload-icon" aria-hidden="true">↑</span><strong>选择客户 Excel 文件</strong><small id="customer-import-file-name">仅支持 .xlsx，本页不会在线修改 Excel 内容</small></label>
        <input id="customer-import-file" name="file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required>
        <div id="customer-import-upload-progress" class="ccg-import-upload-progress import-upload-progress" role="status" aria-live="polite" hidden><span class="import-processing-spinner" aria-hidden="true"></span><span><strong>正在上传并检查…</strong><small>正在创建导入批次并检查 Excel，请勿重复提交。</small></span></div>
        <div id="customer-import-upload-error" class="banner banner-warning" role="alert" hidden></div>
        <div class="import-upload-actions"><button id="customer-import-submit" class="ccg-button ccg-button-primary" type="submit">上传并检查</button></div>
      </form></section>`;
  }

  function uploadResultMarkup(result) {
    const c = contract();
    const validation = result && result.validation;
    if (validation && validation.file_valid === false) {
      const rejectedCopy = uploadCopy(c, 'upload_file_rejected');
      const issueCopy = c && c.issue_copy && typeof c.issue_copy === 'object' ? c.issue_copy : {};
      const fileErrors = Array.isArray(validation.file_errors) ? validation.file_errors : [];
      const messages = fileErrors.map((item) => issueCopy[item && item.code] || rejectedCopy);
      const errors = (messages.length ? messages : [rejectedCopy]).map((message) => `<li>${esc(message)}</li>`).join('');
      return `${importPageHeader('文件校验结果', '该文件未进入批次处理。', '<button class="ccg-button ccg-button-primary" type="button" data-action="import-upload-again">重新选择文件</button>')}
        <div class="banner banner-warning"><strong>${esc(rejectedCopy)}</strong></div>
        <section class="ccg-panel"><div class="ccg-panel-body"><h2 class="import-result-title">${esc(result.source_file || '上传文件')}</h2><ul class="import-message-list">${errors}</ul></div></section>`;
    }
    if (!validation || validation.file_valid !== true) throw new Error('Unexpected customer import upload response');
    const counts = result.validation.counts;
    const hasErrors = Number(counts.error || 0) > 0;
    return `${importPageHeader('文件校验结果', '校验已完成，可继续查看批次。', '<button class="ccg-button ccg-button-secondary" type="button" data-action="import-upload-again">继续上传</button>')}
      <div class="banner ${hasErrors ? 'banner-warning' : 'banner-success'}"><strong>${esc(c.copy.upload_validated)}</strong>${hasErrors ? '<br>请修改 Excel 后重新上传。' : ''}</div>
      <section class="ccg-panel"><div class="ccg-panel-header"><div><h2>${esc(result.source_file)}</h2><p>文件已安全保存为新批次</p></div>${statusBadge(result.status)}</div><div class="ccg-panel-body">${countCards(counts)}
      <div class="import-result-actions"><a class="ccg-button ccg-button-primary" href="${importPath(result.batch_id, 'review')}" data-nav="${importPath(result.batch_id, 'review')}">查看校验明细</a></div></div></section>`;
  }

  function buildTabs(payload) {
    const c = contract();
    const tabs = Object.fromEntries(Object.keys(c.tab_labels || {}).map((name) => [name, []]));
    (payload.rows || []).forEach((row) => {
      const tab = (c.tab_status || {})[row.status];
      if (tab && tabs[tab]) tabs[tab].push(row);
    });
    return tabs;
  }

  function missingReferenceHelp(finding) {
    // Only guide missing references; ambiguous/inactive matches keep their own errors.
    if (finding.code !== 'MDM_IMPORT_REFERENCE_UNRESOLVED' || finding.severity !== 'ERROR'
      || !Array.isArray(finding.details?.candidates) || finding.details.candidates.length) return null;
    return {
      salesrep_ref: { label: '销售代表主档', path: '/mdm/references/salesreps' },
      channel_ref: { label: '渠道主档', path: '/mdm/references/channels' },
    }[finding.field] || null;
  }

  function referenceHelpMarkup(payload) {
    if (!hasPermission('mdm', 'VIEW', state.user)) return '';
    const targets = new Map();
    (payload.rows || []).filter((row) => row.status === 'ERROR').forEach((row) => {
      (row.findings || []).forEach((finding) => {
        const help = missingReferenceHelp(finding);
        if (help) targets.set(help.path, help);
      });
    });
    if (!targets.size) return '';
    const editor = canWrite();
    const links = [...targets.values()].map((help) => `<a class="ccg-button ccg-button-secondary" href="${help.path}" target="_blank" rel="noopener noreferrer">${editor ? '维护' : '查看'}${help.label}（新标签页）</a>`).join('');
    const retry = editor ? '<a class="ccg-button ccg-button-primary" href="/mdm/import-center/upload" data-nav="/mdm/import-center/upload">重新上传原 Excel</a>' : '';
    return `<div class="banner banner-info"><strong>请先补齐销售代表主档，再重新上传</strong><p>${editor ? '请先在对应主档新增，并确认状态为启用。' : '请联系具备 MDM EDIT 权限的用户新增，并确认状态为启用；你只能查看主档。'}主档将在新标签页打开，完成后返回本页。当前批次不会自动重新校验，本页不能直接新增主档。</p><div class="import-result-actions">${links}${retry}</div></div>`;
  }

  function rowMessages(row) {
    const copy = contract().issue_copy || {};
    return (row.findings || []).map((item) => {
      const help = missingReferenceHelp(item);
      return help ? `未匹配：${displayValue(row.normalized_values?.[item.field], '未填写')}。请先在${help.label}新增后重新上传` : copy[item.code];
    }).filter(Boolean);
  }

  function identityMarkup(row) {
    return `<div class="ccg-import-row-identity import-row-identity"><strong>${esc(row.identity.customer_name || '未命名客户')}</strong><span>${esc(row.identity.customer_code || '未填写编码')} · Excel 第 ${Number(row.row_number)} 行</span></div>`;
  }

  function differenceMarkup(difference) {
    const entries = Object.entries(difference || {});
    if (!entries.length) return '<p class="ccg-import-readonly-note import-readonly-note">来源数据与已有主数据一致。</p>';
    return `<dl class="ccg-import-differences import-differences">${entries.map(([field, values]) => `<div><dt>${esc(FIELD_COPY[field] || '字段')}</dt><dd>本次文件：${esc(displayValue(values.source, '未填写'))}</dd><dd>已有数据：${esc(displayValue(values.master, '未填写'))}</dd></div>`).join('')}</dl>`;
  }

  function tabRowsMarkup(name, rows, payload) {
    if (!rows.length) return '<div class="import-tab-empty">该分类暂无数据</div>';
    const errorFirst = Number(payload.counts.error || 0) > 0;
    const canConfirm = canWrite()
      && (contract().reviewable_statuses || []).includes(payload.status) && !errorFirst;
    return `<div class="ccg-import-row-list import-row-list">${rows.map((row) => {
      const messages = rowMessages(row);
      const messageList = messages.length ? `<ul class="import-message-list">${messages.map((message) => `<li>${esc(message)}</li>`).join('')}</ul>` : '';
      let detail = '<p class="ccg-import-readonly-note import-readonly-note">该行仅供查看，无需操作。</p>';
      if (name === 'needs_fix') detail = `${messageList}<p class="ccg-import-readonly-note import-readonly-note">请在 Excel 中修正后重新上传。</p>`;
      if (name === 'needs_confirm') {
        const ids = (row.findings || []).filter((item) => item.severity === 'WARNING' && item.finding_id).map((item) => item.finding_id);
        detail = `${messageList}${canConfirm ? `<button class="ccg-button ccg-button-primary" type="button" data-action="import-confirm-warning" data-row="${Number(row.row_number)}" data-ids="${ids.join(',')}">确认无误，继续导入</button>` : '<p class="ccg-import-readonly-note import-readonly-note">当前暂不能确认该行。</p>'}`;
      }
      if (name === 'existing') {
        const stableId = row.existing && row.existing.matched_stable_id;
        detail = `${messageList}${differenceMarkup(row.existing && row.existing.difference)}${stableId ? `<a class="ccg-button ccg-button-secondary" href="/mdm/customers/${encodeURIComponent(stableId)}" data-nav="/mdm/customers/${encodeURIComponent(stableId)}">查看已有客户</a>` : ''}`;
      }
      return `<article class="ccg-import-row-card import-row-card">${identityMarkup(row)}<div class="import-row-detail">${detail}</div></article>`;
    }).join('')}</div>`;
  }

  function reviewMarkup(payload, activeTab = null, banner = '') {
    const c = contract();
    const tabs = buildTabs(payload);
    const names = Object.keys(c.tab_labels || {});
    const preferred = Number(payload.counts.error || 0) > 0 ? 'needs_fix' : Number(payload.counts.warning || 0) > 0 ? 'needs_confirm' : Number(payload.counts.existing || 0) > 0 ? 'existing' : 'create';
    const selected = names.includes(activeTab) ? activeTab : preferred;
    importState.activeTab = selected;
    const errorBanner = Number(payload.counts.error || 0) > 0 ? `<div class="banner banner-warning import-error-first"><strong>请先处理需要修改的行</strong><br>${esc(c.copy.error_first_blocked)}</div>` : '';
    const successBanner = banner ? `<div class="banner banner-success">${esc(banner)}</div>` : '';
    const tabButtons = names.map((name) => `<button class="ccg-local-tab ccg-import-review-tab import-tab ${name === selected ? 'active' : ''}" type="button" role="tab" aria-selected="${name === selected}" data-action="import-tab" data-tab="${name}">${esc(c.tab_labels[name])}<span>${tabs[name].length}</span></button>`).join('');
    const actions = payload.status === 'READY_TO_COMMIT'
      ? `<a class="ccg-button ccg-button-primary" href="${importPath(payload.batch_id, 'commit')}" data-nav="${importPath(payload.batch_id, 'commit')}">查看提交汇总</a>` : '';
    return `${importPageHeader('批次处理', '先修改错误，再确认业务提示。已存在和可新增行仅供查看。', actions, '<a href="/mdm/import-center" data-nav="/mdm/import-center">数据导入</a> / 批次处理')}${successBanner}${errorBanner}${referenceHelpMarkup(payload)}
      <section class="ccg-panel ccg-import-summary import-summary"><div class="ccg-panel-header"><div><h2>${esc(payload.source_file)}</h2><p>批次 ${esc(payload.batch_id)}</p></div>${statusBadge(payload.status)}</div><div class="ccg-panel-body">${countCards(payload.counts)}</div></section>
      <section class="ccg-panel ccg-import-review-panel import-review-panel">${importReviewToolsMarkup()}<div class="ccg-local-tabs ccg-import-review-tabs import-tabs" role="tablist">${tabButtons}</div><div class="ccg-import-tab-panel import-tab-panel" role="tabpanel">${tabRowsMarkup(selected, tabs[selected], payload)}</div></section>`;
  }

  function commitResultMarkup(payload, result = null) {
    const createdRows = (payload.rows || []).filter((row) => row.status === 'COMMITTED');
    const existingRows = (payload.rows || []).filter((row) => row.status === 'EXISTING');
    const summary = (result && result.result_summary) || { created: createdRows.length, existing: existingRows.length, total: createdRows.length + existingRows.length };
    const rows = [...createdRows.map((row) => ({ ...row, outcome: '已创建' })), ...existingRows.map((row) => ({ ...row, outcome: '已存在' }))];
    return `${importPageHeader('提交结果', '本批次已完成，可返回批次列表继续处理。', '<button class="ccg-button ccg-button-primary" type="button" data-action="import-home">返回批次列表</button>')}
      <div class="banner banner-success"><strong>提交完成，共创建 ${Number(summary.created || 0)} 个客户、${Number(summary.existing || 0)} 个已存在</strong></div>
      <section class="ccg-panel"><div class="ccg-panel-body">${countCards({ total: summary.total, error: 0, warning: 0, existing: summary.existing, ready: summary.created })}
      <div class="ccg-import-result-list import-result-list">${rows.map((row) => `<div>${identityMarkup(row)}<span class="import-outcome">${row.outcome}</span></div>`).join('')}</div></div></section>`;
  }

  function commitMarkup(payload, result = null) {
    const c = contract();
    if (payload.status === 'COMMITTED') return commitResultMarkup(payload, result);
    const admin = canWrite();
    const ready = payload.status === 'READY_TO_COMMIT';
    const notice = !admin ? c.copy.commit_forbidden : !ready ? c.copy.commit_not_ready : '提交后将一次性创建本批次中的新客户，已存在客户不会被修改。';
    const action = admin && ready ? '<button class="ccg-button ccg-button-primary" type="button" data-action="import-open-commit">确认提交客户</button>' : '';
    return `${importPageHeader('提交批次', '提交前请最后确认批次汇总。', '<button class="ccg-button ccg-button-secondary" type="button" data-action="import-back-review">返回批次处理</button>', '<a href="/mdm/import-center" data-nav="/mdm/import-center">数据导入</a> / 提交批次')}
      <section class="ccg-panel ccg-import-commit-card import-commit-card"><div class="ccg-panel-header"><div><h2>${esc(payload.source_file)}</h2><p>批次 ${esc(payload.batch_id)}</p></div>${statusBadge(payload.status)}</div><div class="ccg-panel-body">${countCards(payload.counts)}<div class="banner ${admin && ready ? 'banner-info' : 'banner-warning'}">${esc(notice)}</div><div class="import-result-actions">${action}</div></div></section>`;
  }

  async function renderHome(sequence) {
    setTopbar('数据导入', 'import-center');
    setContent(`${importPageHeader('数据导入', '正在读取客户导入批次。')}${loadingMarkup('table')}`);
    try {
      const payload = await mdmFetch(`${BATCHES_PATH}?page=1&page_size=50`);
      if (sequence === state.routeSequence) setContent(homeMarkup(payload));
    } catch (error) {
      if (sequence === state.routeSequence) setContent(`${importPageHeader('数据导入', '跟踪客户 Excel 导入进度。')}${stateMarkup('error', '批次列表加载失败', '加载失败，请稍后重试', '重试', 'retry-route')}`);
    }
  }

  async function loadReview(batchId, sequence, page, banner = '') {
    setTopbar(page === 'commit' ? '提交批次' : '批次处理', 'import-center');
    setContent(`${importPageHeader(page === 'commit' ? '提交批次' : '批次处理', '正在读取客户导入批次。')}${loadingMarkup()}`);
    try {
      const payload = await mdmFetch(`${BATCHES_PATH}/${encodeURIComponent(batchId)}/review`);
      if (sequence !== state.routeSequence) return;
      importState.review = payload;
      setContent(page === 'commit' ? commitMarkup(payload, importState.commitResult) : reviewMarkup(payload, null, banner));
    } catch (error) {
      if (sequence !== state.routeSequence) return;
      const c = contract();
      const message = error.status === 404 ? c.copy.review_not_found : c.copy.review_load_error;
      setContent(`${importPageHeader(page === 'commit' ? '提交批次' : '批次处理', '客户导入批次。')}${stateMarkup('error', message, message, error.status === 404 ? '' : '重试', error.status === 404 ? '' : 'retry-route')}`);
    }
  }

  async function render(current, sequence) {
    if (current.name === 'import-home') return renderHome(sequence);
    if (current.name === 'import-upload') {
      if (!canWrite()) { setContent(`${importPageHeader('新建客户导入', '上传并校验客户 Excel。')}${stateMarkup('error', '当前为只读权限', '需要 MDM EDIT 才能上传文件。', '返回批次列表', 'import-home')}`); return; }
      importState.uploading = false;
      setTopbar('新建客户导入', 'import-center');
      setContent(uploadFormMarkup());
      return;
    }
    return loadReview(current.batchId, sequence, current.name === 'import-commit' ? 'commit' : 'review');
  }

  async function acknowledgeBatch(payload, findingIds) {
    return mdmFetch(`${BATCHES_PATH}/${encodeURIComponent(payload.batch_id)}/acknowledge`, {
      method: 'POST', body: JSON.stringify({ review_version: payload.review_version, finding_ids: findingIds }),
    });
  }

  async function confirmWarning(target) {
    if (!importState.review || Number(importState.review.counts.error || 0) > 0) return;
    const ids = String(target.dataset.ids || '').split(',').filter(Boolean).map(Number);
    if (!ids.length) return;
    target.disabled = true;
    target.textContent = '确认中…';
    try {
      await acknowledgeBatch(importState.review, ids);
      const count = ids.length;
      await loadReview(importState.review.batch_id, state.routeSequence, 'review', `已确认 ${count} 项，可继续处理`);
    } catch (error) {
      const c = contract();
      showToast(error.code === 'MDM_IMPORT_STALE_REVIEW_VERSION' ? c.copy.acknowledge_stale : c.copy.acknowledge_error);
      target.disabled = false;
      target.textContent = '确认无误，继续导入';
    }
  }

  async function commitBatch(batchId) {
    return mdmFetch(`${BATCHES_PATH}/${encodeURIComponent(batchId)}/commit`, { method: 'POST' });
  }

  function openCommitDialog() {
    if (!importState.review) return;
    const layer = document.getElementById('confirm-layer');
    const confirm = document.getElementById('dialog-confirm');
    document.getElementById('dialog-title').textContent = '确认提交该批次？';
    document.getElementById('dialog-copy').textContent = `文件：${importState.review.source_file}\n将新增 ${Number(importState.review.counts.ready || 0)} 个客户，${Number(importState.review.counts.existing || 0)} 个已存在客户保持不变。\n\n本批次会一次完成，请确认后继续。`;
    document.getElementById('dialog-icon').textContent = '✓';
    document.getElementById('dialog-error').hidden = true;
    confirm.textContent = '确认提交';
    confirm.className = 'ccg-button ccg-button-primary';
    confirm.disabled = false;
    state.dialogAction = async () => {
      confirm.disabled = true;
      confirm.textContent = '提交中…';
      try {
        importState.commitResult = await commitBatch(importState.review.batch_id);
        closeDialog();
        await loadReview(importState.review.batch_id, state.routeSequence, 'commit');
      } catch (_error) {
        const box = document.getElementById('dialog-error');
        box.textContent = contract().copy.commit_server_error;
        box.hidden = false;
        confirm.disabled = false;
        confirm.textContent = '确认提交';
      }
    };
    layer.hidden = false;
    confirm.focus();
  }

  async function submitUpload(form) {
    if (importState.uploading) return;
    const fileInput = form.querySelector('input[type="file"]');
    const file = fileInput && fileInput.files && fileInput.files[0];
    const c = contract();
    if (!file || !file.name.toLowerCase().endsWith('.xlsx')) {
      setContent(uploadFormMarkup(uploadCopy(c, 'upload_not_xlsx'), 'error'));
      return;
    }
    importState.uploading = true;
    setUploadState(form, true);
    const body = new FormData();
    body.append('file', file);
    let errorMessage = '';
    try {
      const result = await mdmFetch(BATCHES_PATH, { method: 'POST', body });
      if (result && typeof result.batch_id === 'string' && result.batch_id) {
        navigate(importPath(result.batch_id, 'review'));
        return;
      }
      setContent(uploadResultMarkup(result));
    } catch (_error) {
      errorMessage = uploadCopy(c, 'upload_server_error');
    } finally {
      importState.uploading = false;
      setUploadState(form, false, errorMessage);
    }
  }

  function setUploadState(form, busy, errorMessage = '') {
    const fileInput = form.querySelector('input[type="file"]');
    const button = document.getElementById('customer-import-submit');
    const progress = document.getElementById('customer-import-upload-progress');
    const error = document.getElementById('customer-import-upload-error');
    if (busy) form.setAttribute('aria-busy', 'true'); else form.removeAttribute('aria-busy');
    if (fileInput) fileInput.disabled = busy;
    if (button) {
      button.disabled = busy;
      button.textContent = busy ? '正在上传并检查…' : '上传并检查';
    }
    if (progress) progress.hidden = !busy;
    if (error) {
      error.textContent = errorMessage;
      error.hidden = !errorMessage;
    }
  }

  function handleAction(action, target) {
    if (!String(action || '').startsWith('import-')) return false;
    if (action === 'import-new' || action === 'import-upload-again') navigate('/mdm/import-center/upload');
    else if (action === 'import-download-template') downloadMdmTemplate('/customer-import/template', 'customer_import_v1_template.xlsx').catch(() => showToast('模板下载失败，请稍后重试'));
    else if (action === 'import-home') navigate('/mdm/import-center');
    else if (action === 'import-back-review' && importState.review) navigate(importPath(importState.review.batch_id, 'review'));
    else if (action === 'import-tab' && importState.review) setContent(reviewMarkup(importState.review, target.dataset.tab));
    else if (action === 'import-confirm-warning') confirmWarning(target);
    else if (action === 'import-open-commit') openCommitDialog();
    return true;
  }

  function handleSubmit(event) {
    if (event.target.id !== 'customer-import-upload-form') return false;
    event.preventDefault();
    submitUpload(event.target);
    return true;
  }

  function handleChange(event) {
    if (event.target.id !== 'customer-import-file') return false;
    const name = event.target.files && event.target.files[0] ? event.target.files[0].name : '仅支持 .xlsx';
    const label = document.getElementById('customer-import-file-name');
    if (label) label.textContent = name;
    return true;
  }

  globalThis.MDMCustomerImport = { routeFromPath, render, handleAction, handleSubmit, handleChange };
  globalThis.MDMCustomerImportTest = {
    configure: (value) => { importState.contract = value; },
    setReview: (value) => { importState.review = value; },
    routeFromPath, homeMarkup, uploadFormMarkup, uploadResultMarkup, buildTabs,
    reviewMarkup, commitMarkup, commitResultMarkup, acknowledgeBatch, commitBatch,
    submitUpload, setUploadState,
  };
}());
