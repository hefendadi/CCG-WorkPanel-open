/* Shared Product/SKU finder for MDM and Sales Actual. */
globalThis.UnifiedProductSkuFinder = (() => {
  const MIN_CHARS = 2;
  const DEBOUNCE_MS = 250;
  const RESULT_LIMIT = 30;

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function labelFor(item) {
    if (!item) return '';
    if (item.type === 'product') return `${item.product_code || ''} · ${item.product_name || ''}`;
    const barcode = item.barcode && item.barcode !== item.sku_code ? ` · ${item.barcode}` : '';
    return `${item.sku_code || ''}${barcode} · ${item.sku_name || ''}`;
  }

  function normalize(type, item) {
    return { ...item, type };
  }

  function balancedResults(products, skus, limit = RESULT_LIMIT) {
    let productLimit = Math.min(products.length, Math.ceil(limit / 2));
    let skuLimit = Math.min(skus.length, Math.floor(limit / 2));
    let remaining = limit - productLimit - skuLimit;
    if (remaining > 0) {
      const moreProducts = Math.min(remaining, products.length - productLimit);
      productLimit += moreProducts;
      remaining -= moreProducts;
      skuLimit += Math.min(remaining, skus.length - skuLimit);
    }
    return {
      products: products.slice(0, productLimit),
      skus: skus.slice(0, skuLimit),
    };
  }

  function resultMarkup(groups) {
    const productRows = groups.products.map((item) => `
      <button type="button" role="option" data-finder-type="product" data-finder-id="${esc(item.stable_id)}">
        <span class="product-sku-finder-code">${esc(item.product_code)}</span>
        <span class="product-sku-finder-name">${esc(item.product_name)}</span>
      </button>`).join('');
    const skuRows = groups.skus.map((item) => {
      const product = item.product ? `${item.product.product_code || ''} · ${item.product.product_name || ''}` : '';
      return `<button type="button" role="option" data-finder-type="sku" data-finder-id="${esc(item.stable_id)}">
        <span class="product-sku-finder-code">${esc(item.sku_code)}</span>
        <span class="product-sku-finder-name">${esc(item.sku_name)}</span>
        <small>Barcode ${esc(item.barcode || item.sku_code || '—')}${product ? ` · 所属 Product ${esc(product)}` : ''}</small>
      </button>`;
    }).join('');
    if (!productRows && !skuRows) return '<div class="product-sku-finder-state">没有匹配的 Product / SKU</div>';
    return `${productRows ? `<div class="product-sku-finder-group"><strong>Product</strong>${productRows}</div>` : ''}
      ${skuRows ? `<div class="product-sku-finder-group"><strong>SKU</strong>${skuRows}</div>` : ''}`;
  }

  function mount(rootOrId, options) {
    const root = typeof rootOrId === 'string' ? document.getElementById(rootOrId) : rootOrId;
    if (!root) return null;
    const fetchJson = options.fetchJson;
    if (typeof fetchJson !== 'function') throw new Error('UnifiedProductSkuFinder requires fetchJson');
    const minChars = options.minChars || MIN_CHARS;
    const debounceMs = options.debounceMs ?? DEBOUNCE_MS;
    const limit = options.limit || RESULT_LIMIT;
    let selection = options.initialSelection ? normalize(options.initialSelection.type, options.initialSelection) : null;
    let timer = null;
    let requestId = 0;
    let itemsByKey = new Map();

    root.classList.add('product-sku-finder');
    root.innerHTML = `<label><span class="product-sku-finder-label">${esc(options.label || '商品搜索')}</span>
      <span class="product-sku-finder-input-wrap"><input type="search" autocomplete="off" spellcheck="false" role="combobox" aria-autocomplete="list" aria-expanded="false" placeholder="${esc(options.placeholder || 'SKU编码 / Barcode / SKU名称 / Product编码 / Product名称')}"><button type="button" class="product-sku-finder-clear" aria-label="清除已选商品" ${selection ? '' : 'hidden'}>×</button></span></label>
      <div class="product-sku-finder-results" role="listbox" hidden></div>`;
    const input = root.querySelector('input');
    const clear = root.querySelector('.product-sku-finder-clear');
    const results = root.querySelector('.product-sku-finder-results');

    function closeResults() {
      results.hidden = true;
      input.setAttribute('aria-expanded', 'false');
    }

    function showState(text, error = false) {
      results.hidden = false;
      results.innerHTML = `<div class="product-sku-finder-state${error ? ' error' : ''}">${esc(text)}</div>`;
      input.setAttribute('aria-expanded', 'true');
    }

    function applySelection(next, notify = true) {
      selection = next ? normalize(next.type, next) : null;
      input.value = labelFor(selection);
      clear.hidden = !selection;
      closeResults();
      if (!notify) return;
      if (selection) options.onSelect?.(selection);
      else options.onClear?.();
    }

    async function search(query, token) {
      showState('正在搜索 Product / SKU…');
      const encoded = encodeURIComponent(query);
      try {
        const [productPage, skuPage] = await Promise.all([
          fetchJson(`/products?status=ALL&search=${encoded}&page=1&page_size=${limit}&sort=product_code&order=asc`),
          fetchJson(`/skus?status=ALL&search=${encoded}&page=1&page_size=${limit}&sort=sku_code&order=asc`),
        ]);
        if (token !== requestId || input.value.trim() !== query) return;
        const groups = balancedResults(
          (productPage.items || []).map((item) => normalize('product', item)),
          (skuPage.items || []).map((item) => normalize('sku', item)),
          limit,
        );
        itemsByKey = new Map([...groups.products, ...groups.skus].map((item) => [`${item.type}:${item.stable_id}`, item]));
        results.innerHTML = resultMarkup(groups);
        results.hidden = false;
        input.setAttribute('aria-expanded', 'true');
      } catch (error) {
        if (token !== requestId) return;
        showState(`商品搜索失败：${error.message || '请稍后重试'}`, true);
      }
    }

    input.addEventListener('input', () => {
      clearTimeout(timer);
      const query = input.value.trim();
      requestId += 1;
      if (!query) {
        if (selection) applySelection(null);
        else closeResults();
        return;
      }
      clear.hidden = false;
      if (query.length < minChars) {
        showState(`请至少输入 ${minChars} 个字符`);
        return;
      }
      const token = requestId;
      timer = setTimeout(() => search(query, token), debounceMs);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeResults();
    });
    clear.addEventListener('click', () => applySelection(null));
    results.addEventListener('click', (event) => {
      const target = event.target.closest('[data-finder-type][data-finder-id]');
      if (!target) return;
      const item = itemsByKey.get(`${target.dataset.finderType}:${target.dataset.finderId}`);
      if (item) applySelection(item);
    });
    if (selection) input.value = labelFor(selection);

    return {
      clear: () => applySelection(null),
      getSelection: () => selection,
      setSelection: (next) => applySelection(next, false),
      search: (query) => {
        input.value = query;
        requestId += 1;
        return search(query, requestId);
      },
    };
  }

  return { mount, resultMarkup, balancedResults, labelFor, MIN_CHARS, DEBOUNCE_MS, RESULT_LIMIT };
})();
