/* UTC date windows and exact wallet matching; shared by UI and unit tests. */
((root) => {
  'use strict';
  const DAY = 86400;
  function parseDay(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) throw new Error('Enter both an initial date and a final date.');
    const ms = Date.parse(value + 'T00:00:00Z');
    if (!Number.isFinite(ms) || new Date(ms).toISOString().slice(0, 10) !== value || ms < 0 || value > '2099-12-31') {
      throw new Error('Use valid calendar dates from 1970 through 2099.');
    }
    return ms / 1000;
  }
  function dateWindow(initial, final) {
    const start = parseDay(initial), lastDay = parseDay(final);
    if (lastDay < start) throw new Error('The final date must be on or after the initial date.');
    // Inclusive calendar dates become [start, midnight after final day) in UTC.
    return {start, end: lastDay + DAY};
  }
  function displayDates(start, end) {
    return {initial: new Date(start * 1000).toISOString().slice(0, 10),
      final: new Date((end - 1) * 1000).toISOString().slice(0, 10)};
  }
  function parseWallets(value = '') {
    if (value.length > 8192) throw new Error('Wallet filter is too long (maximum 8,192 characters).');
    const parts = value.trim() ? value.trim().split(/[\s,;]+/) : [];
    if (parts.some(x => !/^0x[0-9a-f]{40}$/i.test(x))) {
      throw new Error('Use full wallet addresses: 0x followed by 40 hexadecimal characters.');
    }
    const wallets = [...new Set(parts.map(x => x.toLowerCase()))].sort();
    if (wallets.length > 50) throw new Error('Filter at most 50 distinct wallet addresses at a time.');
    return wallets;
  }
  const helpers = {dateWindow, displayDates, parseWallets};
  root.XviFilters = helpers;
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
})(typeof window !== 'undefined' ? window : globalThis);
