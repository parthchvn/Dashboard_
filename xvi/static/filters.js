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
  // Same canonical interval names as the API. Custom input is whole minutes.
  const PRESETS = {auto: null, raw: 0, '30s': 30, '1m': 60, '2m': 120,
    '5m': 300, '10m': 600, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400};
  function normalizeLevel(value) {
    if (Object.hasOwn(PRESETS, value)) return value;
    if (/^[1-9][0-9]{0,3}m$/.test(value || '')) {
      const minutes = Number(value.slice(0, -1));
      if (minutes <= 1440) return Object.keys(PRESETS).find(key => PRESETS[key] === minutes * 60) || value;
    }
    throw new Error('Choose a granularity preset or 1–1440 whole minutes.');
  }
  function granularity(choice, minutes = '') {
    if (choice !== 'custom') return normalizeLevel(choice);
    const text = String(minutes).trim();
    if (!/^[1-9][0-9]{0,3}$/.test(text) || Number(text) > 1440) {
      throw new Error('Custom granularity must be a whole number of minutes from 1 to 1440.');
    }
    return normalizeLevel(text + 'm');
  }
  function granularityControls(level) {
    level = normalizeLevel(level);
    return Object.hasOwn(PRESETS, level) ? {choice: level, minutes: ''} : {choice: 'custom', minutes: level.slice(0, -1)};
  }
  const helpers = {dateWindow, displayDates, parseWallets, normalizeLevel, granularity, granularityControls};
  root.XviFilters = helpers;
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
})(typeof window !== 'undefined' ? window : globalThis);
