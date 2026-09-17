/* Sales Actual Preview + Publish. GET paths never parse/map/publish; the Publish
   action calls the single POST publish endpoint whose gate lives on the server. */
const $ = id => document.getElementById(id);
const batchId = location.pathname.match(/^\/sales\/actual\/import-batches\/([^/]+)\/preview$/)?.[1];
const isDashboardPage = location.pathname === '/sales/actual' || location.pathname === '/sales/actual/';
let salesCanEdit=false;
const labels = {
  kit_parent:'KIT_PARENT',nonpositive_qty:'非正数量',sku_not_found:'SKU Not Found',
  sku_ambiguous:'SKU Ambiguous',product_unassigned:'Product Unassigned',
  customer_unmatched:'Customer Unmatched',channel_salesrep_unassigned:'Channel / SalesRep Unassigned'
};
const reasonCodes = {
  kit_parent:'KIT_PARENT',nonpositive_qty:'NONPOSITIVE_QTY',sku_not_found:'SKU_NOT_FOUND',
  sku_ambiguous:'SKU_AMBIGUOUS',product_unassigned:'PRODUCT_NOT_ASSIGNED',
  customer_unmatched:'CUSTOMER_NOT_FOUND'
};
const gateText={
  BATCH_NOT_PREVIEW_READY:'Batch 尚未完成 Preview，不能发布。',
  PREVIEW_INTEGRITY_INVALID:'Preview 持久化数据不完整，请联系管理员。',
  BATCH_SUPERSEDED:'该版本已被更新的快照替换，不能再发布。',
  DATA_COVERAGE_REGRESSION:'当前已发布数据截至 %s，本次文件仅截至 %s，不能用较早快照覆盖当前数据。',
  DATA_COVERAGE_UNAVAILABLE:'缺少数据截至日期，不能判断累计覆盖范围。',
  CURRENT_MONTH_FACT_INTEGRITY_ERROR:'系统数据完整性异常：当前月份 Fact 与已发布 Batch 不一致。请联系管理员核查，禁止强制发布。',
  ABNORMAL_DROP_THRESHOLD:'本次累计销售数量较当前已发布快照下降 %s%，确认仍然发布。'
};
function fmtYmd(value){return value?String(value).replace(/-/g,'/'):'';}
function percent(value){
  const number=Number(value);
  return Number.isFinite(number)?(number*100).toFixed(1):value;
}
function describe(item){
  const text=gateText[item.code];
  if(!text)return item.code;
  if(item.code==='DATA_COVERAGE_REGRESSION')return text.replace('%s',fmtYmd(item.current_data_end_date)).replace('%s',fmtYmd(item.new_data_end_date));
  if(item.code==='ABNORMAL_DROP_THRESHOLD')return text.replace('%s',percent(item.drop_ratio));
  return text;
}
async function request(path,options={}){
  const response=await authFetch('/api/v1/sales/actual'+path,options);
  const contentType=(response.headers.get('Content-Type')||'').split(';',1)[0].trim().toLowerCase();
  const isJson=contentType==='application/json'||contentType.endsWith('+json');
  let body={};
  if(isJson){
    try{body=await response.json();}
    catch(_error){if(response.ok)throw new Error('服务器响应格式异常');}
  }else if(response.ok){
    throw new Error('服务器响应格式异常');
  }
  if(!response.ok){
    const d=body&&typeof body.detail==='object'&&body.detail?body.detail:{};
    let message=body&&typeof body.detail==='string'?body.detail:undefined;
    if(response.status===413)message='上传文件超过服务器允许的大小（上限 10 MB）。';
    else if(d.code==='DUPLICATE_SOURCE_FILE')message=`文件已存在于 Batch ${d.batch_id}`;
    else if(d.errors&&d.errors.length)message=d.errors.map(e=>e.message).join('；');
    else if(d.gate_status==='REQUIRES_CONFIRMATION')message='发布需要确认，请勾选风险确认后再试。';
    else if(d.blocking&&d.blocking[0])message=describe(d.blocking[0]);
    else if(d.code==='BATCH_SUPERSEDED')message=`该版本已被替换（当前 Batch ${d.current_batch_id||'—'}）。`;
    else if(d.code)message=d.code;
    const error=new Error(message||`请求未完成（HTTP ${response.status}）`);
    error.code=d.code||'';error.detail=d;error.status=response.status;
    throw error;
  }
  return body;
}
if(globalThis.__SALES_ACTUAL_TEST__)globalThis.SalesActualTest={request};
function metric(label,data,tone=''){
  return `<div class="ccg-import-count${tone?` ccg-import-count-${tone}`:''}"><span>${esc(label)}</span><strong>${qty(data.rows)} rows · ${qty(data.qty)} Pcs</strong></div>`;
}
function statusClass(status){
  if(status==='PUBLISHED'||status==='READY')return 'ccg-status-success';
  if(status==='BLOCKED'||status==='SUPERSEDED')return 'ccg-status-danger';
  if(status==='REQUIRES_CONFIRMATION'||status==='PREVIEW_READY')return 'ccg-status-warning';
  return 'ccg-status-neutral';
}
function showMessage(message){
  $('message-copy').textContent=message;
  $('message').hidden=false;
  $('message').scrollIntoView({behavior:'smooth',block:'center'});
}
function clearMessage(){$('message').hidden=true;$('message-copy').textContent='';}
function showContent(stateId,contentId){$(stateId).hidden=true;$(contentId).hidden=false;}
function showLoadingState(stateId,title,message,retryId){
  const state=$(stateId);
  state.hidden=false;
  state.className='ccg-state ccg-state-loading';
  state.querySelector('.ccg-state-icon').textContent='…';
  state.querySelector('h2').textContent=title;
  state.querySelector('p').textContent=message;
  $(retryId).hidden=true;
}
function showLoadError(stateId,title,message,retryId){
  const state=$(stateId);
  state.className='ccg-state ccg-state-error';
  state.querySelector('.ccg-state-icon').textContent='!';
  state.querySelector('h2').textContent=title;
  state.querySelector('p').textContent=message;
  $(retryId).hidden=false;
}
let lastGate=null;
function renderGate(gate){
  lastGate=gate||null;
  const idempotent=Boolean(gate&&gate.idempotent_current);
  const superseded=gate&&gate.blocking&&gate.blocking.some(b=>b.code==='BATCH_SUPERSEDED');
  const badge=idempotent?'PUBLISHED':gate.status;
  $('gate-badge').textContent=badge;
  $('gate-badge').className=`ccg-status sales-gate-status ${statusClass(badge)}`;

  const messages=[];
  for(const item of (gate.blocking||[]))messages.push(describe(item));

  $('gate').className=`sales-gate-message ${statusClass(idempotent?'PUBLISHED':gate.status)}`;
  const brief=idempotent?'该批次已是本月当前已发布快照（重复发布无副作用）。':(messages.length?messages.join(' '):(gate.status==='READY'?'Publish Gate 通过，可以发布当前快照。':'需要确认后发布'));
  $('gate').textContent=brief;

  const confirmations=gate&&gate.required_confirmations?gate.required_confirmations:[];
  const checkbox=$('drop-confirm');
  if(gate.status==='REQUIRES_CONFIRMATION'&&confirmations.length){
    $('confirm-area').hidden=false;
    const risk=confirmations.find(c=>c.code==='ABNORMAL_DROP_THRESHOLD');
    $('confirm-text').textContent=risk?describe(risk):'请确认上述风险后发布。';
    checkbox.checked=false;
  }else{
    $('confirm-area').hidden=true;
    checkbox.checked=false;
  }
  const button=$('publish-button');
  button.hidden=!salesCanEdit;
  button.disabled=!(gate.status==='READY'&&!idempotent&&!superseded);
  button.textContent=idempotent?'已发布':(superseded?'已失效':'发布销售实绩');
  $('publish-step').className=idempotent?'complete':gate.status==='READY'||gate.status==='REQUIRES_CONFIRMATION'?'active':'';
  if(idempotent){
    $('preview-readonly-banner').hidden=false;
    $('preview-readonly-banner').textContent='该 Batch 已发布并成为当前快照，页面保持只读。';
  }
}
async function publishNow(){
  const button=$('publish-button');
  button.disabled=true;
  clearMessage();
  const confirmations=lastGate&&lastGate.status==='REQUIRES_CONFIRMATION'&&$('drop-confirm').checked
    ?(lastGate.required_confirmations||[]).map(item=>({code:item.code}))
    :[];
  try{
    const data=await request(`/import-batches/${encodeURIComponent(batchId)}/publish`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({confirmations})
    });
    await loadPreview();
    $('publish-result').hidden=false;
    $('publish-result').className='sales-publish-result ccg-status-success';
    if(data.idempotent)$('publish-result').textContent='该批次已是当前版本，无需重复发布。';
    else if(data.replaced_batch_id)$('publish-result').textContent=`已更新本月销售实绩，数据截至 ${fmtYmd(data.data_end_date)}。`;
    else $('publish-result').textContent=`已发布销售实绩，数据截至 ${fmtYmd(data.data_end_date)}。`;
    $('publish-result').scrollIntoView({behavior:'smooth',block:'center'});
  }catch(error){
    showMessage(error.message);
    if(lastGate&&lastGate.status==='READY')$('publish-button').disabled=false;
  }
}
function openPublishConfirm(){
  if($('publish-button').disabled)return;
  $('publish-confirm-layer').hidden=false;
  $('publish-confirm-submit').focus();
}
function closePublishConfirm(){$('publish-confirm-layer').hidden=true;$('publish-button').focus();}
function renderIssues(items){
  $('issues').innerHTML='<thead><tr><th>Issue</th><th>External Code</th><th>Name</th><th class="number">Rows</th><th class="number">Qty (Pcs)</th><th>Severity</th></tr></thead><tbody>'+
    (items.map(i=>`<tr><td><span class="ccg-table-code">${esc(i.issue_type)}</span></td><td class="ccg-mono">${esc(i.external_code||'—')}</td><td>${esc(i.external_name||'—')}</td><td class="number">${qty(i.affected_rows)}</td><td class="number">${qty(i.affected_qty)}</td><td><span class="ccg-status ${statusClass(i.severity==='ERROR'?'BLOCKED':'REQUIRES_CONFIRMATION')}">${esc(i.severity)}</span></td></tr>`).join('')||'<tr><td colspan="6" class="empty">没有 Mapping Issue</td></tr>')+'</tbody>';
}
async function showRows(reason,label){
  const data=await request(`/import-batches/${batchId}/rows?mapping_reason=${encodeURIComponent(reason)}&limit=500`);
  $('row-detail').hidden=false;$('row-detail-title').textContent=`${label} · Raw Row 明细（${data.total}）`;
  $('rows').innerHTML='<thead><tr><th>ERP Row</th><th>Document</th><th>Line</th><th>Date</th><th>SKU</th><th>SKU Name</th><th>Customer</th><th class="number">Qty</th></tr></thead><tbody>'+
    (data.items.map(r=>`<tr><td class="number">${r.source_row_no}</td><td class="ccg-mono">${esc(r.source_document_no)}</td><td class="ccg-mono">${esc(r.source_line_no)}</td><td>${esc(r.sales_date)}</td><td class="ccg-mono">${esc(r.source_sku_code||'—')}</td><td>${esc(r.source_sku_name||'—')}</td><td>${esc(r.source_customer_name||'—')}</td><td class="number">${qty(r.actual_qty)}</td></tr>`).join('')||'<tr><td colspan="8" class="empty">暂无 Raw Row</td></tr>')+'</tbody>';
  $('row-detail').scrollIntoView({behavior:'smooth',block:'start'});
}
const aggregateColumns={
  product:[['product_code_snapshot','Product Code'],['product_name_snapshot','Product Name']],
  sku:[['sku_code_snapshot','SKU Code'],['sku_name_snapshot','SKU Name'],['product_code_snapshot','Product Code'],['product_name_snapshot','Product Name']],
  'channel-product':[['channel_code_snapshot','Channel Code'],['channel_name_snapshot','Channel'],['product_code_snapshot','Product Code'],['product_name_snapshot','Product Name']],
  'salesrep-product':[['salesrep_code_snapshot','SalesRep Code'],['salesrep_name_snapshot','SalesRep'],['product_code_snapshot','Product Code'],['product_name_snapshot','Product Name']]
};
async function loadAggregate(dimension){
  [...document.querySelectorAll('#tabs button')].forEach(b=>{const active=b.dataset.dimension===dimension;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));});
  const data=await request(`/import-batches/${batchId}/aggregates/${dimension}?limit=500`);const columns=aggregateColumns[dimension];
  $('aggregate').innerHTML='<thead><tr>'+columns.map(c=>`<th>${esc(c[1])}</th>`).join('')+'<th class="number">Rows</th><th class="number">Qty (Pcs)</th></tr></thead><tbody>'+
    (data.items.map(item=>'<tr>'+columns.map((c,index)=>`<td class="${c[0].includes('code')?'ccg-mono':''}${index===0?' ccg-table-code':''}">${esc(item[c[0]]||'未归属')}</td>`).join('')+`<td class="number">${qty(item.rows)}</td><td class="number">${qty(item.qty)}</td></tr>`).join('')||`<tr><td colspan="${columns.length+2}" class="empty">暂无 READY Candidate</td></tr>`)+'</tbody>';
}
async function loadPreview(){
  $('publish-result').hidden=true;
  clearMessage();
  const data=await request(`/import-batches/${batchId}/preview`),b=data.batch,s=data.summary;
  $('title').textContent=`销售实绩｜数据截至 ${fmtYmd(b.data_date_end)}`;
  $('period').textContent=`${b.snapshot_month.slice(0,7)} · ${b.data_date_start} ～ ${b.data_date_end}`;
  $('file-info').innerHTML=`<strong>${esc(b.filename)}</strong><span>月份 <b class="ccg-tabular">${esc(b.snapshot_month.slice(0,7))}</b></span><span>Batch <b class="ccg-mono">${esc(b.batch_id)}</b></span><span>SHA-256 <b class="ccg-mono">${esc(b.source_file_sha256)}</b></span><span>状态 <b>${esc(b.status)}${b.published_at?` · ${esc(b.published_at)}`:''}</b></span>`;
  $('headline').innerHTML=metric('Raw',s.raw)+metric('Ready',s.ready,'success')+metric('Skipped',s.skipped,s.skipped.rows?'warning':'');
  $('mapping-cards').innerHTML=Object.entries(labels).map(([key,label])=>`<div class="sales-reason"><small>${esc(label)}</small><strong>${qty(s[key].rows)} rows</strong><span>${qty(s[key].qty)} Pcs</span>${reasonCodes[key]?`<button class="ccg-table-link" type="button" data-reason="${reasonCodes[key]}" data-label="${esc(label)}">查看明细</button>`:''}</div>`).join('');
  renderGate(data.publish_gate);renderIssues(data.issues);await loadAggregate('product');showContent('preview-state','preview-content');
}
async function loadBatches(){
  const data=await request('/import-batches?limit=100');
  $('batch-list').innerHTML=data.items.map(b=>`<tr><td><a class="ccg-table-code sales-batch-file" href="/sales/actual/import-batches/${encodeURIComponent(b.batch_id)}/preview">${esc(b.filename)}</a><small class="ccg-mono">${esc(b.batch_id)}</small></td><td class="ccg-tabular">${esc(b.snapshot_month.slice(0,7))}</td><td class="number">${qty(b.ready_rows)}</td><td><span class="ccg-status ${statusClass(b.status)}">${esc(b.status)}</span></td><td><a class="ccg-button ccg-button-secondary" href="/sales/actual/import-batches/${encodeURIComponent(b.batch_id)}/preview">查看 Preview</a></td></tr>`).join('');
  $('batch-empty').hidden=Boolean(data.items.length);
  document.querySelector('.sales-batch-table').hidden=!data.items.length;
  showContent('landing-state','landing-content');
}
async function loadLandingPage(){
  $('landing-content').hidden=true;
  showLoadingState('landing-state','正在加载 Data Maintenance','正在读取现有 Import Batch…','landing-retry');
  try{await loadBatches();}catch(error){showLoadError('landing-state','Data Maintenance 加载失败',error.message,'landing-retry');}
}
async function loadPreviewPage(){
  $('preview-content').hidden=true;
  showLoadingState('preview-state','正在加载 Preview','正在读取冻结数据与 Publish Gate…','preview-retry');
  try{await loadPreview();}catch(error){showLoadError('preview-state','Preview 加载失败',error.message,'preview-retry');}
}
$('file').addEventListener('change',event=>{$('upload-file-name').textContent=event.target.files[0]?.name||'尚未选择文件';});
$('upload-form').addEventListener('submit',async event=>{event.preventDefault();const button=$('upload-button');button.disabled=true;button.textContent='正在生成 Preview…';$('upload-progress').hidden=false;clearMessage();try{const body=new FormData();body.append('file',$('file').files[0]);const created=await request('/import-batches',{method:'POST',body});location.href=created.preview_url;}catch(error){showMessage(error.message);button.disabled=false;button.textContent='上传并生成 Preview';$('upload-progress').hidden=true;}});
$('publish-button').addEventListener('click',openPublishConfirm);
$('publish-confirm-cancel').addEventListener('click',closePublishConfirm);
$('publish-confirm-submit').addEventListener('click',()=>{closePublishConfirm();publishNow().catch(error=>showMessage(error.message));});
$('publish-confirm-layer').addEventListener('click',event=>{if(event.target===$('publish-confirm-layer'))closePublishConfirm();});
$('publish-confirm-layer').addEventListener('keydown',event=>{if(event.key==='Escape')closePublishConfirm();});
$('drop-confirm').addEventListener('change',event=>{$('publish-button').disabled=!(lastGate&&lastGate.status==='REQUIRES_CONFIRMATION'&&event.target.checked);});
$('mapping-cards').addEventListener('click',event=>{const button=event.target.closest('[data-reason]');if(button)showRows(button.dataset.reason,button.dataset.label).catch(error=>showMessage(error.message));});
$('tabs').addEventListener('click',event=>{const button=event.target.closest('[data-dimension]');if(button)loadAggregate(button.dataset.dimension).catch(error=>showMessage(error.message));});
$('close-detail').addEventListener('click',()=>{$('row-detail').hidden=true;});
$('message-dismiss').addEventListener('click',clearMessage);
$('landing-retry').addEventListener('click',loadLandingPage);
$('preview-retry').addEventListener('click',loadPreviewPage);
(async()=>{if(isDashboardPage)return;if(!await requireLogin())return;salesCanEdit=hasPermission('sales_actual','EDIT');$('user').textContent=currentUser.username||'';const maintenanceLink=document.querySelector('[data-nav="import"]');if(maintenanceLink){maintenanceLink.hidden=!salesCanEdit;maintenanceLink.classList.toggle('active',true);maintenanceLink.setAttribute('aria-current','page');}if(!salesCanEdit){$('upload-panel').hidden=true;$('readonly-banner').hidden=false;$('preview-readonly-banner').hidden=false;}if(batchId){$('preview').hidden=false;await loadPreviewPage();}else{$('landing').hidden=false;await loadLandingPage();}})();
