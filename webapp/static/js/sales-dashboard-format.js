/* Exact display formatting for API decimal strings. No Number conversion. */
(function expose(root) {
  function parts(value) {
    const raw = String(value ?? '').trim().replaceAll(',', '');
    const match = raw.match(/^(-?)(\d+)(?:\.(\d+))?$/);
    if (!match) return null;
    return { sign: match[1], whole: match[2], fraction: match[3] || '' };
  }

  function qty(value) {
    const parsed = parts(value);
    if (!parsed) return '—';
    const whole = parsed.whole.replace(/^0+(?=\d)/, '');
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    const fraction = parsed.fraction.slice(0, 4).replace(/0+$/, '');
    return `${parsed.sign}${grouped}${fraction ? `.${fraction}` : ''}`;
  }

  function percentValue(value) {
    const parsed = parts(value);
    if (!parsed || parsed.sign === '-') return null;
    const fraction = parsed.fraction.padEnd(4, '0');
    let tenths = BigInt(`${parsed.whole}${fraction.slice(0, 3)}`);
    if (fraction[3] >= '5') tenths += 1n;
    const digits = tenths.toString().padStart(2, '0');
    return `${digits.slice(0, -1)}.${digits.slice(-1)}`;
  }

  function share(value) {
    const percent = percentValue(value);
    return percent === null ? '—' : `${percent}%`;
  }

  const formatter = { qty, share, percentValue };
  root.salesDashboardFormat = formatter;
  if (typeof module !== 'undefined' && module.exports) module.exports = formatter;
}(typeof globalThis === 'undefined' ? this : globalThis));
