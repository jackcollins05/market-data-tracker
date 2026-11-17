/* Pure data and calculation helpers. All analytics run in the browser. */
export const COLUMNS = ['date', 'item_id', 'item', 'source', 'open', 'high', 'low', 'close', 'volume', 'trading_date'];
export const PRICE_FIELDS = ['open', 'high', 'low', 'close'];
export const NUMERIC_FIELDS = [...PRICE_FIELDS, 'volume'];
export const WATCHLIST = [
  ['AAPL', 'Apple Inc.'], ['MSFT', 'Microsoft Corp.'], ['NVDA', 'NVIDIA Corp.'],
  ['AMZN', 'Amazon.com Inc.'], ['GOOGL', 'Alphabet Inc. (Class A)'],
  ['META', 'Meta Platforms Inc.'], ['TSLA', 'Tesla Inc.'],
  ['JPM', 'JPMorgan Chase & Co.'], ['BAC', 'Bank of America Corp.'],
  ['WMT', 'Walmart Inc.'], ['XOM', 'Exxon Mobil Corp.'], ['JNJ', 'Johnson & Johnson'],
];
export const NAMES = new Map(WATCHLIST);

export function parseCSV(input) {
  const text = String(input).replace(/^\uFEFF/, '');
  const records = [];
  let record = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"' && field === '') quoted = true;
    else if (char === ',') { record.push(field); field = ''; }
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[i + 1] === '\n') i += 1;
      record.push(field); field = '';
      if (record.some((value) => value !== '')) records.push(record);
      record = [];
    } else field += char;
  }
  if (quoted) throw new Error('History CSV has an unfinished quoted value.');
  if (field !== '' || record.length) { record.push(field); records.push(record); }
  if (!records.length || COLUMNS.some((name, index) => records[0][index] !== name) || records[0].length !== COLUMNS.length) {
    throw new Error('History CSV columns do not match the backend contract.');
  }
  if (records.slice(1).some((record) => record.length !== COLUMNS.length)) {
    throw new Error('History CSV has a row with an unexpected number of columns.');
  }
  return records.slice(1).map((record) =>
    Object.fromEntries(COLUMNS.map((name, index) => [name, record[index]])));
}

export function numberOrNull(value) {
  if (value === '' || value === null || value === undefined) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function normalizeHistory(csvText) {
  return parseCSV(csvText).filter((row) => validDate(row.date) && validDate(row.trading_date) && row.trading_date <= row.date && row.item_id).map((row) => {
    const normalized = { ...row };
    for (const field of NUMERIC_FIELDS) normalized[field] = numberOrNull(row[field]);
    return normalized;
  }).sort((a, b) => a.date.localeCompare(b.date) || a.item_id.localeCompare(b.item_id));
}

export function validateStatus(status) {
  const required = ['run_time', 'first_run_date', 'days_with_success', 'items', 'new_today', 'gone_today', 'sources', 'observation_date_utc'];
  if (!status || typeof status !== 'object' || required.some((key) => !(key in status)) ||
      !validDate(status.observation_date_utc) || !Array.isArray(status.sources) ||
      !Array.isArray(status.new_today) || !Array.isArray(status.gone_today)) {
    throw new Error('Status JSON does not match the backend contract.');
  }
  return status;
}

export function latestByTicker(rows) {
  const result = new Map();
  for (const row of rows) {
    const old = result.get(row.item_id);
    if (!old || row.date > old.date || (row.date === old.date && row.trading_date > old.trading_date)) result.set(row.item_id, row);
  }
  return result;
}

export function previousObservation(rows, current) {
  if (!current) return null;
  let previous = null;
  for (const row of rows) {
    if (row.item_id !== current.item_id || row.date >= current.date) continue;
    if (!previous || row.date > previous.date) previous = row;
  }
  return previous;
}

export function compareClose(current, previous) {
  if (!current || !previous) return { kind: 'first', change: null, percent: null };
  if (current.trading_date <= previous.trading_date) return { kind: 'unchanged-bar', change: null, percent: null };
  if (current.close === null || previous.close === null || previous.close <= 0) return { kind: 'unavailable', change: null, percent: null };
  const change = current.close - previous.close;
  const percent = change / previous.close * 100;
  return Number.isFinite(percent) ? { kind: 'change', change, percent } : { kind: 'unavailable', change: null, percent: null };
}

export function tradingSessions(rows, ticker) {
  const sessions = new Map();
  for (const row of rows) {
    if (row.item_id !== ticker || !validDate(row.trading_date)) continue;
    const old = sessions.get(row.trading_date);
    if (!old || row.date > old.date) sessions.set(row.trading_date, row);
  }
  return [...sessions.values()].sort((a, b) => a.trading_date.localeCompare(b.trading_date));
}

export function sourceState(status, row) {
  const failure = (status.failures || []).find((entry) => entry.item_id === (row && row.item_id));
  if (failure) return 'Failed latest request';
  if (!row || row.date !== status.observation_date_utc) return 'No snapshot today';
  return 'Recorded today';
}

export function filterBrowse(tickers, latest, status, options) {
  const { query = '', priceBand = 'all', observation = 'all', favoritesOnly = false,
    favorites = new Set(), sort = null, direction = 'asc' } = options;
  const needle = query.trim().toLowerCase();
  const shown = tickers.filter((ticker) => {
    const row = latest.get(ticker);
    return (!needle || (NAMES.get(ticker) || ticker).toLowerCase().includes(needle) || ticker.toLowerCase().includes(needle)) &&
      (priceBand === 'all' || (priceBand === 'unavailable' ? !row || row.close === null :
        row && row.close !== null && (priceBand === 'under-100' ? row.close < 100 :
          priceBand === '100-199' ? row.close >= 100 && row.close < 200 :
            priceBand === '200-plus' && row.close >= 200))) &&
      (observation === 'all' || sourceState(status, row || { item_id: ticker }) === observation) &&
      (!favoritesOnly || favorites.has(ticker));
  });
  if (sort && NUMERIC_FIELDS.includes(sort)) shown.sort((a, b) => {
    const left = latest.get(a) ? latest.get(a)[sort] : null;
    const right = latest.get(b) ? latest.get(b)[sort] : null;
    if (left === null || left === undefined) return right === null || right === undefined ? a.localeCompare(b) : 1;
    if (right === null || right === undefined) return -1;
    return (left - right) * (direction === 'asc' ? 1 : -1) || a.localeCompare(b);
  });
  return shown;
}

export function readFavorites(storage) {
  try {
    const saved = JSON.parse(storage.getItem('mdt-favorites-v1') || '[]');
    return new Set(Array.isArray(saved) ? saved.filter((value) => typeof value === 'string') : []);
  } catch { return new Set(); }
}

export function writeFavorites(storage, favorites) {
  try { storage.setItem('mdt-favorites-v1', JSON.stringify([...favorites])); return true; }
  catch { return false; }
}

export function calculateFindings(rows) {
  const tickers = [...new Set(rows.map((row) => row.item_id))];
  const performance = [], volatility = [], volume = [], range = [];
  for (const ticker of tickers) {
    const sessions = tradingSessions(rows, ticker);
    const closes = sessions.filter((row) => row.close !== null && row.close > 0);
    if (closes.length >= 2) {
      const first = closes[0], last = closes[closes.length - 1];
      performance.push({ ticker, value: (last.close - first.close) / first.close * 100, first: first.close, last: last.close, firstDate: first.trading_date, lastDate: last.trading_date });
    }
    const returns = [];
    for (let i = 1; i < sessions.length; i += 1) {
      const prior = sessions[i - 1].close, latest = sessions[i].close;
      if (prior !== null && prior > 0 && latest !== null) returns.push((latest - prior) / prior * 100);
      const priorVolume = sessions[i - 1].volume, latestVolume = sessions[i].volume;
      if (priorVolume !== null && priorVolume > 0 && latestVolume !== null && latestVolume >= 0) {
        volume.push({ ticker, value: (latestVolume - priorVolume) / priorVolume * 100, first: priorVolume, last: latestVolume, firstDate: sessions[i - 1].trading_date, lastDate: sessions[i].trading_date });
      }
    }
    if (returns.length >= 2) {
      const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
      const variance = returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (returns.length - 1);
      volatility.push({ ticker, value: Math.sqrt(variance), count: returns.length });
    }
    const ranges = sessions.filter((row) => row.open !== null && row.open > 0 && row.high !== null && row.low !== null && row.high >= row.low)
      .map((row) => (row.high - row.low) / row.open * 100);
    if (ranges.length) range.push({ ticker, value: ranges.reduce((sum, value) => sum + value, 0) / ranges.length, count: ranges.length });
  }
  performance.sort((a, b) => b.value - a.value || a.ticker.localeCompare(b.ticker));
  volatility.sort((a, b) => b.value - a.value || a.ticker.localeCompare(b.ticker));
  volume.sort((a, b) => Math.abs(b.value) - Math.abs(a.value) || a.ticker.localeCompare(b.ticker));
  range.sort((a, b) => b.value - a.value || a.ticker.localeCompare(b.ticker));
  return { performance, volatility, volume, range };
}

export const formatMoney = (value) => value === null || !Number.isFinite(value) ? '—' :
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
export const formatNumber = (value) => value === null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('en-US').format(value);
export const formatPercent = (value, signed = false) => value === null || !Number.isFinite(value) ? '—' : `${signed && value > 0 ? '+' : ''}${value.toFixed(2)}%`;
export const formatDate = (value) => validDate(value) ? new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(`${value}T00:00:00Z`)) : 'Unavailable';
export const formatTimestamp = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unavailable' : `${new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short' }).format(date)} UTC`;
};
