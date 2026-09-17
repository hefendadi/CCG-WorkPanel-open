/* Native HTML workbench; only explicit Save creates a 销售预测版本. */
const calc = OrderingCalculation;
const $ = id => document.getElementById(id);
const cycleId = location.pathname.match(/^\/ordering\/cycles\/(\d+)$/)?.[1];
let model, draft, options, saving = false;
let orderingCanEdit = false;
const expanded = new Set();
const label = v => ({OPEN:'进行中',LOCKED:'已锁定',CONFIRMED:'已确认',UNCONFIRMED:'未确认',Missing:'未填写',ACTUAL:'实际销售',INVENTORY:'库存','Baseline Missing':'缺少库存基准','Baseline Conflict':'库存基准冲突'}[v] || v);
const display = value => calc.missing(value) ? '<span class="missing">缺少数据</span>' : esc(calc.decimal(calc.units(value)));
async function request(path, method = 'GET', body) {
  const response = await authFetch('/api/v1/ordering' + path, {
    method, headers: {'Content-Type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' && /[\u3400-\u9fff]/.test(data.detail) ? data.detail : '请求未完成，请检查所选来源、月份和数量后重试。');
  return data;
}
function message(error) { $('message').textContent = error ? (/[\u3400-\u9fff]/.test(error.message) ? error.message : '操作未完成，请检查连接后重试。') : ''; }
function totals(row) {
  try { return calc.calculate(row, model.baseline_state === 'Ready'); }
  catch (_) { return {forecast: [null,null,null,null], projected: [null,null,null,null]}; }
}
function summaryCells(row) {
  const t = totals(row);
  return [0,1,2,3].map(m => `<td>${display(row.incoming[m])}</td><td class="forecast" data-total="${row.id}-${m}">${display(t.forecast[m])}</td><td class="projected" data-projected="${row.id}-${m}">${display(t.projected[m])}</td>`).join('');
}
function detailHtml(row) {
  const d = row.details;
  if (!d) return '';
  const month = v => v ? esc(v.slice(0,7)) : '未知';
  return `<tr class="explanation" ${expanded.has(row.id)?'':'hidden'}><td colspan="19"><div class="detail-panel">
    <section><h3>SKU 在库状态</h3><p>${esc(d.reconciliation)} · SKU 合计：${display(d.sku_total)} · 产品当前库存：${display(row.opening)}</p>
    <table><thead><tr><th>SKU编码</th><th>SKU名称</th><th>${model.baseline_month.slice(0,7)} 月末库存</th><th>占产品库存比例</th></tr></thead><tbody>
    ${d.sku_inventory.map(s => `<tr><td>${esc(s.code)}</td><td>${esc(s.name)}</td><td>${display(s.ending_qty)}</td><td>${s.proportion === null?'不可计算':esc(s.proportion)+'%'}</td></tr>`).join('') || '<tr><td colspan="4">暂无可展示的非零库存事实</td></tr>'}</tbody></table></section>
    <section><h3>批次消耗</h3><p>${esc(d.lot_message)}</p><table><thead><tr><th>生产日期</th><th>当前状态</th><th>首次入库月</th><th>售罄月</th><th>消耗月数</th><th>当前剩余库存</th><th>统计截至</th><th>来源版本</th></tr></thead><tbody>
    ${d.lots.map(l => `<tr><td>${esc(l.production_date)}</td><td>${esc(l.status_label)}</td><td>${month(l.first_inbound_month)}</td><td>${l.status==='SOLD_OUT'?month(l.final_sold_out_month):'—'}</td><td>${l.consumption_months === null ? (l.status==='ACTIVE'?'消耗中':'未知') : esc(l.consumption_months)+' 个月'}</td><td>${display(l.remaining_qty)}</td><td>${month(l.observed_through)}</td><td>${l.source_version_ids.map(id=>'#'+id).join('、') || '—'}</td></tr>`).join('') || '<tr><td colspan="8">暂无匹配的已保存批次结果</td></tr>'}</tbody></table></section>
    <h3>渠道销售预测</h3></div></td></tr>`;
}
function renderTable() {
  $('grid').innerHTML = `<thead><tr><th rowspan="2" class="left code">产品编码</th><th rowspan="2" class="left">产品名称</th><th rowspan="2">当前库存<br>${esc(model.baseline_month.slice(0,7))} 月末</th>${model.months.map((m,i) => `<th colspan="3">${i ? 'T+'+i : 'T'} · ${esc(m.slice(0,7))}</th>`).join('')}<th rowspan="2">未排期<br>在途</th><th rowspan="2">逾期<br>在途</th><th rowspan="2" class="decision final-qty">最终订货量</th><th rowspan="2" class="decision final-status">确认状态</th></tr><tr>${model.months.map(() => '<th>在途来货</th><th>销售预测</th><th>预计月末库存</th>').join('')}</tr></thead><tbody>${draft.rows.map(row => `<tr><td class="left code">${esc(row.code || '—')}</td><td class="name"><button class="expand" data-expand="${row.id}" aria-expanded="${expanded.has(row.id)}" aria-label="展开 ${esc(row.name)} 渠道">${expanded.has(row.id)?'−':'+'}</button>${esc(row.name)}</td><td>${display(row.opening)}</td>${summaryCells(row)}<td>${display(row.unscheduled)}</td><td>${display(row.overdue)}</td><td class="decision final-qty">${display(row.final_order)}</td><td class="decision final-status">${esc(label(row.confirmation))}</td></tr>${detailHtml(row)}${row.channels.map(ch => `<tr class="channel" data-channel-product="${row.id}" ${expanded.has(row.id)?'':'hidden'}><td class="code"></td><td class="left">${esc(ch.name)}</td><td></td>${model.months.map((month,m) => `<td></td><td><input type="text" inputmode="decimal" placeholder="未填写" aria-label="${esc(row.name)} ${esc(ch.name)} ${month.slice(0,7)} 销售预测" data-product="${row.id}" data-channel="${ch.id}" data-month="${m}" value="${esc(ch.forecast[m] ?? '')}" ${orderingCanEdit && model.save_allowed && !saving?'':'disabled'}></td><td></td>`).join('')}<td colspan="4"></td></tr>`).join('')}`).join('')}</tbody>`;
  if (!draft.rows.length) $('grid').insertAdjacentHTML('beforeend', '<caption>暂无产品。请选择已有事实来源。</caption>');
}
function updateDraftState() {
  $('draft-state').textContent = draft.dirty ? '有未保存修改' : '已保存';
  $('draft-state').className = draft.dirty ? 'dirty' : '';
  let valid = true;
  try { draft.payload(); } catch (_) { valid = false; }
  $('save').disabled = !orderingCanEdit || saving || !model.save_allowed || !draft.dirty || !valid;
  $('sources-button').disabled = !orderingCanEdit || saving || draft.dirty || model.status === 'LOCKED';
  $('sources-button').title = draft.dirty ? '请先保存销售预测草稿，再切换来源' : '';
}
function render() {
  $('title').textContent = model.name;
  $('period').textContent = `${model.months[0].slice(0,7)} — ${model.months[3].slice(0,7)} · 周期月份为销售预测起始月`;
  $('status').textContent = label(model.status);
  const sourceText = kind => model.sources[kind].length ? model.sources[kind].map(s => `#${s.id} ${esc(s.label)} · ${s.start.slice(0,7)}～${s.end.slice(0,7)}`).join('<br>') : '缺少数据';
  $('source-state').innerHTML = `<div class="card"><small>实际销售来源</small>${sourceText('ACTUAL')}</div><div class="card"><small>库存来源</small>${sourceText('INVENTORY')}</div><div class="card"><small>在途来货快照</small>${model.incoming_snapshot ? '#'+model.incoming_snapshot.id+' · '+esc(model.incoming_snapshot.name) : '缺少数据'}</div><div class="card"><small>销售预测版本</small>${model.forecast_version ? 'V'+model.forecast_version.number+' · #'+model.forecast_version.id : '缺少数据'}</div><div class="card"><small>最终订货确认</small>${model.confirmation.confirmed} / ${model.confirmation.total} 已填写订单已确认</div>`;
  $('baseline').textContent = model.baseline_state === 'Ready' ? `库存基准：已绑定 ${model.baseline_month.slice(0,7)} 期末库存。缺少某产品库存时，该产品显示缺少数据。` : `${label(model.baseline_state)}：必须唯一绑定 ${model.baseline_month.slice(0,7)} 库存；整个 T～T+3 库存推演已暂停。`;
  $('baseline').className = 'notice' + (model.baseline_state === 'Ready' ? '' : ' problem');
  $('gaps').hidden = !model.gaps.length;
  $('gaps').textContent = model.gaps.join(' ');
  renderTable(); updateDraftState();
}
async function load() {
  model = await request(`/cycles/${cycleId}`);
  draft = calc.createDraft(model);
  render();
}
$('grid').addEventListener('click', event => {
  const button = event.target.closest('[data-expand]');
  if (!button) return;
  const id = Number(button.dataset.expand);
  expanded.has(id) ? expanded.delete(id) : expanded.add(id);
  renderTable();
});
$('grid').addEventListener('input', event => {
  const input = event.target.closest('input[data-product]');
  if (!input || saving) return;
  const pid = Number(input.dataset.product);
  draft.edit(pid, Number(input.dataset.channel), Number(input.dataset.month), input.value);
  let valid = true;
  try { const q=calc.units(input.value); if(q!==null && (q<0n || q>=1000000000000000000n)) valid=false; } catch(_){valid=false;}
  input.setAttribute('aria-invalid', String(!valid));
  const t = totals(draft.rows.find(r => r.id === pid));
  for (let m=0;m<4;m++) {
    document.querySelector(`[data-total="${pid}-${m}"]`).innerHTML = display(t.forecast[m]);
    document.querySelector(`[data-projected="${pid}-${m}"]`).innerHTML = display(t.projected[m]);
  }
  updateDraftState();
});
$('save').addEventListener('click', async () => {
  if (saving || !draft.dirty) return;
  try {
    const payload = draft.payload();
    saving = true; renderTable(); updateDraftState(); message();
    const saved = await request(`/cycles/${cycleId}/forecast`, 'POST', payload);
    // Clear the acknowledged draft even if a subsequent read fails.
    model.rows = JSON.parse(JSON.stringify(draft.rows));
    model.forecast_version = saved;
    draft = calc.createDraft(model);
    await load();
  } catch (e) { message(e); }
  finally { saving = false; render(); }
});
window.addEventListener('beforeunload', event => {
  if (draft?.dirty) { event.preventDefault(); event.returnValue = ''; }
});
$('sources-button').addEventListener('click', async () => {
  if (draft.dirty || model.status === 'LOCKED') return;
  try {
    options = await request(`/cycles/${cycleId}/source-options`);
    const selected = Object.values(model.sources).flat();
    $('dataset-options').innerHTML = options.datasets.map(v => {
      const bindings = selected.filter(b => b.id === v.id);
      // Retain every existing slice; otherwise offer one explicit full-coverage slice.
      return (bindings.length ? bindings : [{start:v.start,end:v.end}]).map(b => `<div class="dataset"><input type="checkbox" aria-label="选择 ${label(v.type)} ${v.id}" data-version="${v.id}" ${bindings.length?'checked':''}><span>${esc(label(v.type))} #${v.id} ${esc(v.label)} · ${esc(v.name || '')}</span><input type="month" aria-label="使用起始月份" value="${b.start.slice(0,7)}"><span style="flex:0;min-width:0">至</span><input type="month" aria-label="使用结束月份" value="${b.end.slice(0,7)}"></div>`).join('');
    }).join('') || '<p>尚无已导入的实际销售或库存版本。</p>';
    $('incoming-select').innerHTML = '<option value="">不选择 / 缺少数据</option>'+options.incoming.map(v => `<option value="${v.id}">#${v.id} ${esc(v.name)} · ${esc(v.at)}</option>`).join('');
    $('forecast-select').innerHTML = '<option value="">不选择 / 缺少数据</option>'+options.forecasts.map(v => `<option value="${v.id}">V${v.number} · #${v.id}</option>`).join('');
    $('incoming-select').value = model.incoming_snapshot?.id ?? '';
    $('forecast-select').value = model.forecast_version?.id ?? '';
    $('source-error').textContent = '';
    $('source-dialog').showModal();
  } catch(e) {message(e);}
});
$('cancel-sources').addEventListener('click', () => $('source-dialog').close());
$('source-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const bindings = [...document.querySelectorAll('.dataset')].filter(el => el.querySelector('[type=checkbox]').checked).map(el => {
      const dates = el.querySelectorAll('[type=month]');
      return {dataset_version_id:Number(el.querySelector('[type=checkbox]').dataset.version),period_start:dates[0].value+'-01',period_end:dates[1].value+'-01'};
    });
    await request(`/cycles/${cycleId}/sources`, 'PUT', {bindings,incoming_snapshot_id:Number($('incoming-select').value)||null,forecast_version_id:Number($('forecast-select').value)||null});
    $('source-dialog').close(); await load();
  } catch(e) {$('source-error').textContent=/[\u3400-\u9fff]/.test(e.message)?e.message:'来源选择未完成，请检查连接后重试。';} finally {button.disabled=false;}
});
$('create').addEventListener('submit', async event => {
  event.preventDefault(); const button=event.submitter; button.disabled=true;
  try {
    const data=new FormData(event.target);
    const result=await request('/cycles','POST',{name:data.get('name'),cycle_month:data.get('month')+'-01'});
    location.href='/ordering/cycles/'+result.id;
  } catch(e) {message(e); button.disabled=false;}
});
(async () => {
  if (!await requireLogin()) return;
  orderingCanEdit=hasPermission('ordering','EDIT');
  $('user').textContent=currentUser.username || '';
  $('create').hidden=!orderingCanEdit;
  $('save').hidden=!orderingCanEdit;
  $('sources-button').hidden=!orderingCanEdit;
  try {
    if(cycleId) {$('workbench').hidden=false; await load();}
    else {
      $('cycles').hidden=false;
      const cycles=await request('/cycles');
      $('cycle-list').innerHTML=cycles.map(c => `<a href="/ordering/cycles/${c.id}"><strong>${esc(c.name)}</strong><span>${esc(c.month.slice(0,7))} · ${esc(label(c.status))}</span></a>`).join('') || '<p class="muted">还没有周期。请选择 T 月份并创建。</p>';
    }
  } catch(e) {message(e);}
})();
