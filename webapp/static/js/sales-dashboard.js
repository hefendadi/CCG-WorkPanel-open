/* Sales Actual Dashboard. Every data request is bound to context.current_batch. */
(async function initDashboardPage(){
  if(!isDashboardPage)return;
  const user=await requireLogin();
  if(!user)return;
  if(!hasPermission('sales_actual','VIEW',user)){location.href='/';return;}
  $('user').textContent=currentUser.username||'';
  document.querySelector('[data-nav="dashboard"]').classList.add('active');
  const maintenanceLink=document.querySelector('[data-nav="import"]');
  if(maintenanceLink)maintenanceLink.hidden=!hasPermission('sales_actual','EDIT',user);
  $('dashboard').hidden=false;

  const format=globalThis.salesDashboardFormat;
  const initialQuery=new URLSearchParams(location.search||'');
  const state={batchId:null,context:null,view:'product',autoReloaded:false,loadToken:0,skuRequestToken:0,pages:{product:1,salesrep:1,channel:1},limit:20,openProduct:null,finderSelection:null,finder:null,pendingDimensions:{channel_id:initialQuery.get('channel_id'),salesrep_id:initialQuery.get('salesrep_id')}};

  function params(extra={}){
    const query=new URLSearchParams({batch_id:state.batchId,...extra});
    if(state.finderSelection){
      const key=state.finderSelection.type==='product'?'product_stable_id':'sku_stable_id';
      query.append(key,state.finderSelection.stable_id);
    }
    for(const [id,key] of [['filter-channel','channel_id'],['filter-salesrep','salesrep_id']]){
      const value=$(id).value;
      if(value)query.append(key,value);
    }
    return `?${query.toString()}`;
  }
  function updateUrl(){
    const query=new URLSearchParams();
    const month=$('filter-month').value;
    if(month)query.set('month',month);
    if(state.finderSelection){
      query.set(state.finderSelection.type==='product'?'product_stable_id':'sku_stable_id',state.finderSelection.stable_id);
    }
    if($('filter-channel').value)query.set('channel_id',$('filter-channel').value);
    if($('filter-salesrep').value)query.set('salesrep_id',$('filter-salesrep').value);
    const encoded=query.toString();
    history.replaceState({},'',`/sales/actual${encoded?`?${encoded}`:''}`);
  }
  async function mdmRequest(path){
    const response=await authFetch(`/api/v1/mdm${path}`);
    let payload={};
    try{payload=await response.json();}catch(_error){payload={};}
    if(!response.ok){
      const message=payload?.error?.message||`商品搜索请求失败（HTTP ${response.status}）`;
      throw new Error(message);
    }
    return payload;
  }
  async function restoreFinderSelection(){
    const skuStableId=initialQuery.get('sku_stable_id');
    const productStableId=initialQuery.get('product_stable_id');
    const type=skuStableId?'sku':productStableId?'product':null;
    const stableId=skuStableId||productStableId;
    if(!type)return null;
    try{return {...await mdmRequest(`/${type==='sku'?'skus':'products'}/${encodeURIComponent(stableId)}`),type};}
    catch(_error){return null;}
  }
  function showState(kind,title,detail,{retry=false}={}){
    const box=$('dashboard-state');
    box.hidden=kind==='ready';
    box.className=`ccg-state ccg-state-${kind} sales-dashboard-state`;
    $('dashboard-state-icon').textContent=kind==='error'?'!':kind==='empty'?'0':'…';
    $('state-title').textContent=title;
    $('state-detail').textContent=detail||'';
    $('dashboard-retry').hidden=!retry;
  }
  function stopQuantities(){
    for(const id of ['kpi-mtd','kpi-products','kpi-skus'])$(id).textContent='—';
    $('dashboard-content').hidden=true;
  }
  function optionValue(item){return item.id===null?'unassigned':String(item.id);}
  function replaceOptions(id,items,placeholder,label){
    const select=$(id),selected=select.value,selectedLabel=select.selectedOptions[0]?.textContent||selected;
    select.innerHTML=`<option value="">${esc(placeholder)}</option>`+items.map(item=>`<option value="${esc(optionValue(item))}">${esc(label(item))}</option>`).join('');
    if(selected&&!([...select.options].some(option=>option.value===selected))){
      select.insertAdjacentHTML('beforeend',`<option value="${esc(selected)}">${esc(selectedLabel)}</option>`);
    }
    if(selected)select.value=selected;
  }
  function renderOptions(data){
    let restoredDimension=false;
    replaceOptions('filter-channel',data.channels,'全部 Channel',item=>item.name||'未归属');
    replaceOptions('filter-salesrep',data.salesreps,'全部 SalesRep',item=>item.name||'未归属');
    for(const [id,key] of [['filter-channel','channel_id'],['filter-salesrep','salesrep_id']]){
      const requested=state.pendingDimensions[key];
      if(requested&&[...$(id).options].some(option=>option.value===requested)){
        $(id).value=requested;
        restoredDimension=true;
      }
      state.pendingDimensions[key]=null;
    }
    return restoredDimension;
  }
  function emptyRow(columns,text='当前筛选条件下暂无数据'){
    return `<tr><td colspan="${columns}" class="ccg-table-empty"><div class="ccg-state ccg-state-empty" role="status"><div class="ccg-state-icon" aria-hidden="true">0</div><div class="ccg-state-copy"><h2>${esc(text)}</h2><p>请调整筛选条件后重试。</p></div></div></td></tr>`;
  }
  function renderPager(name,pagination){
    const pageCount=Math.max(1,Math.ceil(pagination.total_items/pagination.page_size));
    $(`${name}-page-info`).textContent=`第 ${pagination.page} / ${pageCount} 页 · ${pagination.total_items} 条`;
    for(const button of document.querySelectorAll(`[data-page="${name}"]`)){
      button.disabled=button.dataset.delta==='-1'?pagination.page<=1:pagination.page>=pageCount;
    }
  }
  function renderSummary(data){
    $('kpi-mtd').textContent=format.qty(data.mtd_qty);
    $('kpi-products').textContent=format.qty(String(data.product_count));
    $('kpi-skus').textContent=format.qty(String(data.sku_count));
  }
  function productRow(item){
    const expanded=state.openProduct===String(item.product_id);
    return `<tr class="sales-product-row${expanded?' is-expanded':''}"><td><button class="ccg-table-link sales-product-trigger" type="button" data-product-id="${esc(item.product_id)}" data-product-name="${esc(item.product_name)}" aria-expanded="${expanded}" aria-label="${expanded?'收起':'展开'} ${esc(item.product_name)} SKU"><span class="sales-disclosure-icon" aria-hidden="true">${expanded?'▾':'›'}</span><span>${esc(item.product_name)}</span></button></td><td class="number">${format.qty(item.sku_count)}</td><td class="number">${format.qty(item.qty)}</td><td class="number">${format.share(item.share_of_filtered_result)}</td></tr>`;
  }
  function renderProducts(data){
    $('product-table').innerHTML='<thead><tr><th>Product</th><th class="number">SKU 数</th><th class="number">出库数量 (Pcs)</th><th class="number">占当前结果</th></tr></thead><tbody>'+(
      data.items.map(productRow).join('')||emptyRow(4)
    )+'</tbody>';
    renderPager('product',data.pagination);
  }
  function renderMatrix(id,data,key,label){
    $(id).innerHTML=`<thead><tr><th>${label}</th><th>Product</th><th class="number">出库数量 (Pcs)</th><th class="number">占当前结果</th></tr></thead><tbody>`+(
      data.items.map(item=>`<tr><td class="dimension-cell">${esc(item[key]||'未归属')}</td><td>${esc(item.product_name)}</td><td class="number">${format.qty(item.qty)}</td><td class="number">${format.share(item.share_of_filtered_result)}</td></tr>`).join('')||emptyRow(4)
    )+'</tbody>';
  }
  function setView(view,{focus=false}={}){
    if(!['product','channel','salesrep'].includes(view))return;
    state.view=view;
    for(const button of document.querySelectorAll('#analysis-tabs [data-view]')){
      const active=button.dataset.view===view;
      button.classList.toggle('active',active);
      button.setAttribute('aria-selected',String(active));
      button.tabIndex=active?0:-1;
      if(active&&focus)button.focus();
    }
    document.querySelectorAll('[data-view-panel]').forEach(panel=>{panel.hidden=panel.dataset.viewPanel!==view;});
  }
  function productButton(productId){
    return [...document.querySelectorAll('[data-product-id]')].find(button=>button.dataset.productId===String(productId));
  }
  function setProductExpanded(button,expanded){
    if(!button)return;
    button.setAttribute('aria-expanded',String(expanded));
    button.setAttribute('aria-label',`${expanded?'收起':'展开'} ${button.dataset.productName} SKU`);
    const icon=button.querySelector('.sales-disclosure-icon');
    if(icon)icon.textContent=expanded?'▾':'›';
    button.closest('tr')?.classList.toggle('is-expanded',expanded);
  }
  function skuStateRow(productId,content,kind='loading'){
    return `<tr id="sku-drilldown" class="sales-sku-detail-row sales-sku-state-row ${kind}" data-sku-parent="${esc(productId)}"><td colspan="4"><div class="sales-inline-state" role="status" aria-live="polite">${content}</div></td></tr>`;
  }
  function skuRows(items,productId){
    if(!items.length)return skuStateRow(productId,'该 Product 暂无 SKU 数据','empty');
    return items.map(item=>`<tr class="sales-sku-detail-row sales-sku-row" data-sku-parent="${esc(productId)}"><td class="sales-sku-cell"><span class="sales-sku-code">${esc(item.sku_code)}</span>${item.sku_name&&item.sku_name!==item.sku_code?`<span class="sales-sku-name">${esc(item.sku_name)}</span>`:''}</td><td aria-hidden="true"></td><td class="number">${format.qty(item.qty)}</td><td aria-hidden="true"></td></tr>`).join('');
  }
  function closeSku(){
    state.skuRequestToken+=1;
    state.openProduct=null;
    document.querySelectorAll('.sales-sku-detail-row').forEach(row=>row.remove());
    document.querySelectorAll('[data-product-id]').forEach(button=>setProductExpanded(button,false));
  }
  async function openSku(productId,productName,{force=false}={}){
    if(!force&&state.openProduct===String(productId)){closeSku();return;}
    closeSku();
    state.openProduct=String(productId);
    const button=productButton(productId);
    setProductExpanded(button,true);
    const parentRow=button?.closest('tr');
    if(!parentRow)return;
    parentRow.insertAdjacentHTML('afterend',skuStateRow(productId,'正在加载 SKU…'));
    const requestToken=++state.skuRequestToken;
    const loadingRow=parentRow.nextElementSibling;
    try{
      const data=await request(`/dashboard/products/${encodeURIComponent(productId)}/skus${params({page:'1',limit:'100',sort:'-qty'})}`);
      if(requestToken!==state.skuRequestToken||state.openProduct!==String(productId))return;
      loadingRow.insertAdjacentHTML('beforebegin',skuRows(data.items||[],productId));
      loadingRow.remove();
    }catch(error){
      if(requestToken!==state.skuRequestToken||state.openProduct!==String(productId))return;
      if(error.code==='DASHBOARD_SNAPSHOT_CHANGED')await handleError(error);
      else loadingRow.outerHTML=skuStateRow(productId,`<span>${esc(error.message)}</span><button type="button" class="ccg-table-link" data-sku-retry="${esc(productId)}" data-product-name="${esc(productName)}">重试</button>`,'error');
    }
  }
  async function handleError(error){
    if(error.code==='DASHBOARD_SNAPSHOT_CHANGED'&&!state.autoReloaded){
      state.autoReloaded=true;
      await loadContext($('filter-month').value||null);
      return;
    }
    stopQuantities();
    if(error.code==='DASHBOARD_FACT_INTEGRITY_ERROR')showState('error','销售实绩完整性校验失败','Fact 与 Current Batch 不一致，数量展示已停止。请重试或联系管理员。',{retry:true});
    else showState('error','销售实绩加载失败',error.message,{retry:true});
  }
  async function loadData(){
    const token=++state.loadToken;
    showState('loading','正在加载销售实绩','正在校验当前已发布数据并读取聚合结果…');
    closeSku();
    try{
      const [options,summary,products,reps,channels]=await Promise.all([
        request(`/dashboard/filter-options${params({include_product_sku:'false'})}`),
        request(`/dashboard/summary${params()}`),
        request(`/dashboard/products${params({page:String(state.pages.product),limit:String(state.limit),sort:'-qty'})}`),
        request(`/dashboard/salesrep-products${params({page:String(state.pages.salesrep),limit:String(state.limit),sort:'-qty'})}`),
        request(`/dashboard/channel-products${params({page:String(state.pages.channel),limit:String(state.limit),sort:'-qty'})}`)
      ]);
      if(token!==state.loadToken)return;
      if(renderOptions(options)){
        resetPages();
        await loadData();
        return;
      }
      renderSummary(summary);
      renderProducts(products);
      renderMatrix('salesrep-table',reps,'salesrep_name','SalesRep');
      renderPager('salesrep',reps.pagination);
      renderMatrix('channel-table',channels,'channel_name','Channel');
      renderPager('channel',channels.pagination);
      $('dashboard-content').hidden=false;
      setView(state.view);
      showState('ready','','');
      updateUrl();
    }catch(error){if(token===state.loadToken)await handleError(error);}
  }
  function renderMonths(context){
    $('filter-month').innerHTML=context.available_months.map(month=>`<option value="${esc(month)}">${esc(monthLabel(month))}</option>`).join('');
    if(context.snapshot_month)$('filter-month').value=context.snapshot_month;
  }
  function monthLabel(value){
    const [year,month]=String(value||'').split('-');
    return year&&month?`${year}年${parseInt(month,10)}月`:String(value||'');
  }
  function dateLabel(value){
    const parts=String(value||'').split('-');
    return parts.length===3?`${parseInt(parts[1],10)}月${parseInt(parts[2],10)}日`:String(value||'');
  }
  async function loadContext(month=null){
    showState('loading','正在加载销售实绩','正在选择当前已发布月份…');
    stopQuantities();
    try{
      const context=await request(`/dashboard/context${month?`?month=${encodeURIComponent(month)}`:''}`);
      state.context=context;
      renderMonths(context);
      if(!context.current_batch){
        state.batchId=null;
        $('dashboard-title').textContent='销售实绩';
        $('current-batch').innerHTML='';
        showState('empty','暂无已发布销售实绩','请前往 Import 上传并发布销售实绩后再查看 Dashboard。');
        return;
      }
      state.batchId=context.current_batch.batch_id;
      $('dashboard-title').textContent='销售实绩';
      $('dashboard-subtitle').textContent='按 Product、Channel 与销售代表查看当前月 ERP 出库实绩。';
      $('current-batch').innerHTML=`<span>${esc(monthLabel(context.snapshot_month))}</span><span class="context-dot">·</span><span>数据截至 ${esc(dateLabel(context.data_end_date))}</span><span class="context-dot">·</span><span>Batch</span><strong>${esc(context.current_batch.batch_id)}</strong>`;
      await loadData();
    }catch(error){await handleError(error);}
  }
  function resetPages(){state.pages={product:1,salesrep:1,channel:1};state.autoReloaded=false;}

  $('filter-month').addEventListener('change',event=>{
    resetPages();
    state.finderSelection=null;
    state.finder?.setSelection(null);
    for(const id of ['filter-channel','filter-salesrep'])$(id).value='';
    loadContext(event.target.value);
  });
  for(const id of ['filter-channel','filter-salesrep'])$(id).addEventListener('change',()=>{
    resetPages();
    loadData();
  });
  $('clear-filters').addEventListener('click',()=>{
    state.finderSelection=null;
    state.finder?.setSelection(null);
    for(const id of ['filter-channel','filter-salesrep'])$(id).value='';
    resetPages();
    loadData();
  });
  $('dashboard-retry').addEventListener('click',()=>{state.autoReloaded=false;loadContext($('filter-month').value||null);});
  $('product-table').addEventListener('click',event=>{
    const retry=event.target.closest('[data-sku-retry]');
    if(retry){openSku(retry.dataset.skuRetry,retry.dataset.productName,{force:true});return;}
    const target=event.target.closest('[data-product-id]');
    if(target)openSku(target.dataset.productId,target.dataset.productName);
  });
  $('analysis-tabs').addEventListener('click',event=>{
    const target=event.target.closest('[data-view]');
    if(target)setView(target.dataset.view);
  });
  $('analysis-tabs').addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const tabs=[...document.querySelectorAll('#analysis-tabs [data-view]')];
    const current=tabs.findIndex(tab=>tab.dataset.view===state.view);
    const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(current+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    event.preventDefault();
    setView(tabs[next].dataset.view,{focus:true});
  });
  document.querySelectorAll('[data-page]').forEach(button=>button.addEventListener('click',()=>{
    const name=button.dataset.page;
    state.pages[name]+=parseInt(button.dataset.delta,10);
    loadData();
  }));

  state.finderSelection=await restoreFinderSelection();
  state.finder=globalThis.UnifiedProductSkuFinder.mount('sales-product-sku-finder',{
    fetchJson:mdmRequest,
    initialSelection:state.finderSelection,
    onSelect(item){state.finderSelection=item;resetPages();updateUrl();if(state.batchId)loadData();},
    onClear(){state.finderSelection=null;resetPages();updateUrl();if(state.batchId)loadData();},
  });
  await loadContext(initialQuery.get('month'));
})();
