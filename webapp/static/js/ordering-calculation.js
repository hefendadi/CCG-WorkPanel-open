/* Fixed-point arithmetic preserves the Core's NUMERIC(18,3), including large quantities. */
(function (root) {
  const missing = v => v === null || v === undefined || String(v).trim() === '';
  function units(value) {
    if (missing(value)) return null;
    const text = String(value).trim();
    if (!/^-?\d+(\.\d{1,3})?$/.test(text)) throw new Error('数量最多保留三位小数');
    const negative = text.startsWith('-');
    const [whole, fraction = ''] = text.replace(/^-/, '').split('.');
    const n = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, '0'));
    return negative ? -n : n;
  }
  function decimal(n) {
    if (n === null) return null;
    const sign = n < 0n ? '-' : '';
    const abs = n < 0n ? -n : n;
    return sign + (abs / 1000n) + (abs % 1000n ? '.' + String(abs % 1000n).padStart(3, '0').replace(/0+$/, '') : '');
  }
  function forecastTotal(channels, month) {
    if (!channels.length || channels.some(c => missing(c.forecast[month]))) return null;
    return decimal(channels.reduce((sum, c) => sum + units(c.forecast[month]), 0n));
  }
  function calculate(row, baselineReady) {
    const forecast = [0, 1, 2, 3].map(m => forecastTotal(row.channels, m));
    let opening = baselineReady ? units(row.opening) : null;
    const projected = forecast.map((f, m) => {
      const incoming = units(row.incoming[m]);
      const demand = units(f);
      opening = opening === null || incoming === null || demand === null ? null : opening + incoming - demand;
      return decimal(opening);
    });
    return {forecast, projected};
  }
  function createDraft(model) {
    let original = JSON.stringify(model.rows);
    const draft = {
      rows: JSON.parse(original),
      get dirty() { return JSON.stringify(this.rows) !== original; },
      edit(productId, channelId, month, value) {
        if (!model.save_allowed) throw new Error('当前周期不可编辑');
        const row = this.rows.find(r => r.id === productId);
        const channel = row.channels.find(c => c.id === channelId);
        channel.forecast[month] = missing(value) ? null : value;
      },
      payload() {
        const lines = [];
        for (const row of this.rows) for (const c of row.channels) for (let m = 0; m < 4; m++) {
          const q = units(c.forecast[m]);
          if (q === null) continue;
          if (q < 0n || q >= 1000000000000000000n) throw new Error('销售预测必须为非负数，整数少于十六位、小数最多三位');
          lines.push({product_stable_id: row.stable_id, channel_stable_id: c.stable_id,
            forecast_month: model.months[m], forecast_qty: decimal(q)});
        }
        return {expected_version_id: model.forecast_version?.id ?? null, lines};
      }
    };
    return draft;
  }
  const api = {units, decimal, forecastTotal, calculate, createDraft, missing};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.OrderingCalculation = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
