import {
  WATCHLIST, NAMES, NUMERIC_FIELDS, latestByTicker, previousObservation, compareClose,
  tradingSessions, calculateFindings, sourceState, filterBrowse, readFavorites, writeFavorites, formatMoney, formatNumber,
  formatPercent, formatDate, formatTimestamp, validDate,
} from './core.js';
import { isDemoMode, pageHref, loadDataset } from './data.js';
import { lineChart, barChart } from './charts.js';

const byId = (id) => document.getElementById(id);
const node = (tag, className, value) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = String(value);
  return element;
};
const set = (id, text) => { byId(id).textContent = text; };
const company = (ticker) => NAMES.get(ticker) || ticker;
const allTickers = (rows) => [...new Set([...WATCHLIST.map(([ticker]) => ticker), ...rows.map((row) => row.item_id)])];
const signedMoney = (value) => value === null ? '—' : `${value > 0 ? '+' : value < 0 ? '−' : ''}${formatMoney(Math.abs(value))}`;
const movementClass = (value) => value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';

function setupShell(demo) {
  const page = document.body.dataset.page;
  for (const link of document.querySelectorAll('[data-nav]')) {
    link.href = pageHref(link.dataset.nav === 'today' ? 'index' : link.dataset.nav, demo);
    if (link.dataset.nav === page) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  document.querySelector('.brand').href = pageHref('index', demo);
  byId('demo-banner').hidden = !demo;
}

function showError(message, demo) {
  const box = node('div', 'panel data-error');
  const missingProduction = !demo && message === 'Production data has not been collected yet.';
  box.append(node('h2', '', missingProduction ? message : demo ? 'Demonstration data could not be displayed' : 'Market data could not be displayed'));
  if (!missingProduction) box.append(node('p', 'subtle', message));
  if (!demo) {
    const link = node('a', 'button-link', 'View clearly labeled synthetic demo data →');
    link.href = pageHref('index', true);
    box.append(link);
  }
  byId('data-message').replaceChildren(box);
  const page = document.body.dataset.page;
  if (page === 'today') {
    const freshness = byId('freshness');
    freshness.classList.remove('fresh');
    freshness.classList.add('stale');
    freshness.replaceChildren(node('span', 'freshness-dot'), node('span', '', 'Data unavailable'));
  } else if (page === 'browse') {
    set('browse-date', '—');
    set('browse-freshness', 'Data unavailable');
  } else if (page === 'trends') {
    set('history-span', '—');
    set('session-count', 'Data unavailable');
    set('trends-freshness', '');
  }
}

function metric(label, value, note, tone = '') {
  const card = node('article', `metric-card ${tone}`);
  card.append(node('span', 'metric-label', label), node('strong', 'metric-value', value), node('span', 'metric-note', note));
  return card;
}

function stockCell(ticker, row) {
  const cell = node('td', 'stock-cell');
  cell.append(node('span', 'ticker', ticker), node('span', 'company', company(ticker)));
  if (row) cell.append(node('span', 'stock-date', `Observed ${formatDate(row.date)} · traded ${formatDate(row.trading_date)}`));
  return cell;
}

function textCell(text, className = '') { return node('td', className, text); }

function renderToday({ rows, status, demo }) {
  byId('today-content').hidden = false;
  const latest = latestByTicker(rows);
  const tickers = allTickers(rows);
  const observedToday = [...latest.values()].filter((row) => row.date === status.observation_date_utc);
  const latestTrading = observedToday.map((row) => row.trading_date).filter(validDate).sort().at(-1) ||
    [...latest.values()].map((row) => row.trading_date).filter(validDate).sort().at(-1);
  set('run-time', status.run_ok ? formatTimestamp(status.run_time) : 'No successful result in latest run');
  set('observation-date', formatDate(status.observation_date_utc));
  set('trading-date', formatDate(latestTrading));
  const currentDate = new Date().toISOString().slice(0, 10);
  const freshness = byId('freshness');
  const fresh = !demo && status.run_ok === true && status.observation_date_utc === currentDate;
  freshness.classList.toggle('fresh', fresh);
  freshness.classList.toggle('stale', !fresh);
  freshness.replaceChildren(node('span', 'freshness-dot'), node('span', '', demo ? `Synthetic observation · ${formatDate(status.observation_date_utc)}` : fresh ? 'Current UTC observation' : `Historical observation · ${formatDate(status.observation_date_utc)}`));

  const successfulSources = status.sources.filter((source) => source.ok === true).length;
  const incompleteSources = status.sources.length - successfulSources;
  const historicalOnly = WATCHLIST.filter(([ticker]) => latest.has(ticker) && latest.get(ticker).date < status.observation_date_utc).length;
  const neverObserved = WATCHLIST.filter(([ticker]) => !latest.has(ticker)).length;
  const successfulRequests = status.sources.reduce((total, source) => total + (Number.isFinite(source.count) ? source.count : 0), 0);
  const failedRequests = Array.isArray(status.failures) ? status.failures.length : 0;
  byId('summary-metrics').replaceChildren(
    metric('LATEST-DATE SNAPSHOTS', formatNumber(status.items), `${WATCHLIST.length} configured · ${historicalOnly} historical only${neverObserved ? ` · ${neverObserved} never observed` : ''}`),
    metric('COMPLETE SOURCES', formatNumber(successfulSources), 'Sources where every request succeeded', successfulSources ? 'good' : ''),
    metric('INCOMPLETE SOURCES', formatNumber(incompleteSources), `${successfulRequests} successful requests · ${failedRequests} failed ${failedRequests === 1 ? 'request' : 'requests'}`, incompleteSources ? 'bad' : ''),
    metric('OBSERVATION DATES', formatNumber(status.days_with_success), 'Distinct UTC dates with snapshots'),
    metric('NEW ITEMS', formatNumber(status.new_today.length), 'First seen on latest UTC date'),
    metric('DISAPPEARED', formatNumber(status.gone_today.length), 'Confirmed only; failures excluded')
  );

  const body = byId('overview-body');
  body.replaceChildren();
  const movers = [];
  for (const ticker of tickers) {
    const row = latest.get(ticker);
    const previous = previousObservation(rows, row);
    const comparison = compareClose(row, previous);
    const tr = node('tr');
    tr.append(stockCell(ticker, row));
    for (const field of ['open', 'close', 'high', 'low']) tr.append(textCell(row ? formatMoney(row[field]) : '—', 'numeric'));
    tr.append(textCell(row ? formatNumber(row.volume) : '—', 'numeric'));
    const changeCell = node('td', `numeric ${comparison.kind === 'change' ? movementClass(comparison.change) : 'neutral'}`);
    if (comparison.kind === 'change') {
      changeCell.append(node('strong', '', signedMoney(comparison.change)), node('span', 'cell-sub', formatPercent(comparison.percent, true)));
      movers.push({ ticker, name: company(ticker), old: previous.close, recent: row.close, ...comparison });
    } else changeCell.textContent = comparison.kind === 'unchanged-bar' ? 'No new bar' : comparison.kind === 'first' ? 'First observation' : 'Unavailable';
    tr.append(changeCell);
    const state = sourceState(status, row);
    const statusCell = node('td');
    statusCell.append(node('span', `status-pill ${state === 'Recorded today' ? 'status-good' : state === 'Failed latest request' ? 'status-bad' : 'status-neutral'}`, state));
    statusCell.append(node('span', 'cell-sub', row ? `Observed ${formatDate(row.date)}` : 'No recorded value'));
    tr.append(statusCell);
    body.append(tr);
  }
  set('overview-count', `${WATCHLIST.length} configured · ${status.items} latest-date snapshots · ${historicalOnly} historical only`);
  renderMovers('gainers', movers.filter((item) => item.change > 0).sort((a, b) => b.percent - a.percent).slice(0, 3), 'No positive move from a newer trading bar.');
  renderMovers('decliners', movers.filter((item) => item.change < 0).sort((a, b) => a.percent - b.percent).slice(0, 3), 'No negative move from a newer trading bar.');
  renderSources(status, latest);
}

function renderMovers(targetId, entries, emptyText) {
  const target = byId(targetId);
  target.replaceChildren();
  if (!entries.length) { target.append(node('p', 'subtle empty-copy', emptyText)); return; }
  for (const entry of entries) {
    const line = node('div', 'mover-row');
    const left = node('div');
    left.append(node('strong', 'ticker', entry.ticker), node('span', 'company', entry.name));
    const right = node('div', 'mover-values');
    right.append(node('strong', movementClass(entry.percent), formatPercent(entry.percent, true)), node('span', 'subtle', `${formatMoney(entry.old)} → ${formatMoney(entry.recent)}`));
    line.append(left, right);
    target.append(line);
  }
}

function renderSources(status, latest) {
  const list = byId('source-list');
  list.replaceChildren();
  for (const source of status.sources) {
    const card = node('article', 'panel source-card');
    const top = node('div', 'source-top');
    top.append(node('h3', '', source.name || 'Unnamed source'), node('span', `status-pill ${source.ok ? 'status-good' : 'status-bad'}`, source.ok ? 'Complete' : 'Incomplete'));
    card.append(top, node('p', 'source-count', `${formatNumber(source.count)} successful requests in latest run`));
    const failures = Array.isArray(status.failures) ? status.failures : [];
    if (failures.length) {
      const items = node('ul', 'failure-list');
      for (const failure of failures) items.append(node('li', '', `${failure.item_id || 'Unknown stock'} · ${failure.category || 'Request failure'}${failure.http_status ? ` · HTTP ${failure.http_status}` : ''}`));
      card.append(items);
    } else card.append(node('p', 'subtle', 'No failed stock requests reported.'));
    list.append(card);
  }
  const historicalOnly = WATCHLIST.map(([ticker]) => ticker).filter((ticker) => latest.has(ticker) && latest.get(ticker).date < status.observation_date_utc);
  const neverObserved = WATCHLIST.map(([ticker]) => ticker).filter((ticker) => !latest.has(ticker));
  const explanation = [
    `${formatNumber(status.items)} valid stock snapshots exist on the latest UTC observation date; source completeness describes the latest request run, not the existence of older history.`,
    historicalOnly.length ? `${historicalOnly.join(', ')} has older history but no snapshot on that date.` : '',
    neverObserved.length ? `${neverObserved.join(', ')} has no recorded history yet.` : '',
    'A failed request is not evidence of disappearance.',
  ].filter(Boolean).join(' ');
  set('source-explainer', explanation);
}

function renderBrowse({ rows, status, demo }) {
  byId('browse-content').hidden = false;
  set('browse-date', formatDate(status.observation_date_utc));
  set('browse-freshness', demo ? 'Synthetic observation' : status.observation_date_utc === new Date().toISOString().slice(0, 10) ? 'Current UTC observation' : 'Historical observation');
  const latest = latestByTicker(rows);
  const tickers = allTickers(rows);
  let storage;
  try { storage = window.localStorage; } catch { storage = null; }
  const favorites = readFavorites(storage);
  const state = { sort: null, direction: 'asc' };
  const render = () => {
    const query = byId('stock-search').value.trim().toLowerCase();
    const priceBand = byId('price-filter').value;
    const observation = byId('state-filter').value;
    const onlyFavorites = byId('favorites-only').checked;
    const shown = filterBrowse(tickers, latest, status, { query, priceBand, observation, favoritesOnly: onlyFavorites,
      favorites, sort: state.sort, direction: state.direction });
    const body = byId('browse-body');
    body.replaceChildren();
    for (const ticker of shown) {
      const row = latest.get(ticker), tr = node('tr');
      const starCell = node('td');
      const star = node('button', `star-button ${favorites.has(ticker) ? 'is-favorite' : ''}`, favorites.has(ticker) ? '★' : '☆');
      star.type = 'button'; star.setAttribute('aria-label', `${favorites.has(ticker) ? 'Remove' : 'Add'} ${company(ticker)} ${favorites.has(ticker) ? 'from' : 'to'} favorites`);
      star.setAttribute('aria-pressed', String(favorites.has(ticker)));
      star.addEventListener('click', () => {
        if (favorites.has(ticker)) favorites.delete(ticker); else favorites.add(ticker);
        const stored = writeFavorites(storage, favorites);
        byId('storage-note').textContent = stored ? '' : 'Favorites are available for this session; browser storage is unavailable.';
        render();
      });
      starCell.append(star); tr.append(starCell, stockCell(ticker, row), textCell('Not supplied', 'subtle'));
      const sourceCell = node('td');
      sourceCell.append(node('span', '', row && row.source || 'Unavailable'), node('span', 'cell-sub', sourceState(status, row || { item_id: ticker })));
      tr.append(sourceCell);
      for (const field of NUMERIC_FIELDS) tr.append(textCell(row ? field === 'volume' ? formatNumber(row[field]) : formatMoney(row[field]) : '—', 'numeric'));
      tr.append(textCell(row ? formatDate(row.trading_date) : '—'));
      body.append(tr);
    }
    set('browse-count', `${shown.length} of ${tickers.length} stocks`);
    byId('browse-empty').hidden = shown.length !== 0;
  };
  for (const id of ['stock-search', 'price-filter', 'state-filter', 'favorites-only']) byId(id).addEventListener(id === 'stock-search' ? 'input' : 'change', render);
  for (const button of document.querySelectorAll('.sort-button')) {
    button.addEventListener('click', () => {
      const field = button.dataset.sort;
      state.direction = state.sort === field && state.direction === 'asc' ? 'desc' : 'asc';
      state.sort = field;
      for (const other of document.querySelectorAll('.sort-button')) {
        const active = other === button;
        other.parentElement.setAttribute('aria-sort', active ? (state.direction === 'asc' ? 'ascending' : 'descending') : 'none');
        other.querySelector('span').textContent = active ? (state.direction === 'asc' ? '↑' : '↓') : '↕';
      }
      render();
    });
  }
  render();
}

function chartCard(title, description, unit) {
  const card = node('article', 'panel chart-card');
  const heading = node('div', 'chart-heading');
  const left = node('div');
  left.append(node('h3', '', title), node('p', 'subtle', description));
  heading.append(left, node('span', 'unit-tag', unit));
  const plot = node('div', 'chart-plot');
  card.append(heading, plot);
  return { card, plot };
}

function renderTrends({ rows, status, demo }) {
  byId('trends-content').hidden = false;
  const dates = [...new Set(rows.map((row) => row.date))].sort();
  set('history-span', dates.length ? `${formatDate(dates[0])} — ${formatDate(dates[dates.length - 1])}` : 'No history');
  set('session-count', `${status.days_with_success} UTC observation dates · ${[...new Set(rows.map((row) => `${row.item_id}:${row.trading_date}`))].length} stock sessions`);
  set('trends-freshness', demo ? 'Synthetic data · not live' : status.observation_date_utc === new Date().toISOString().slice(0, 10) ? 'Latest observation is today (UTC)' : `Latest observation: ${formatDate(status.observation_date_utc)} UTC`);
  const tickers = allTickers(rows).filter((ticker) => rows.some((row) => row.item_id === ticker));
  const selected = new Set(tickers.slice(0, 3));
  const selector = byId('stock-selector');
  selector.replaceChildren();
  const metricLabels = { open: 'Opening price', high: 'Daily high', low: 'Daily low', close: 'Closing price', volume: 'Trading volume' };
  const chartRefs = new Map();
  const chartStack = byId('history-charts');
  chartStack.replaceChildren();
  for (const field of NUMERIC_FIELDS) {
    const { card, plot } = chartCard(metricLabels[field], field === 'volume' ? 'Shares traded per source trading session.' : 'USD per share on each source trading session.', field === 'volume' ? 'SHARES' : 'USD / SHARE');
    chartRefs.set(field, plot);
    chartStack.append(card);
  }
  const draw = () => {
    for (const field of NUMERIC_FIELDS) {
      const series = [...selected].map((ticker) => ({ ticker, points: tradingSessions(rows, ticker).map((row) => ({ date: row.trading_date, value: row[field] })) }));
      lineChart(chartRefs.get(field), series, { title: `${metricLabels[field]} by trading date`, unit: field === 'volume' ? 'shares' : 'USD per share', valueLabel: field === 'volume' ? (value) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value) : (value) => `$${value.toFixed(0)}` });
    }
  };
  for (const ticker of tickers) {
    const label = node('label', 'stock-choice');
    const input = node('input'); input.type = 'checkbox'; input.value = ticker; input.checked = selected.has(ticker);
    input.addEventListener('change', () => { if (input.checked) selected.add(ticker); else selected.delete(ticker); draw(); });
    label.append(input, node('span', '', ticker)); selector.append(label);
  }
  byId('select-all').addEventListener('click', () => { for (const input of selector.querySelectorAll('input')) { input.checked = true; selected.add(input.value); } draw(); });
  byId('clear-all').addEventListener('click', () => { for (const input of selector.querySelectorAll('input')) input.checked = false; selected.clear(); draw(); });
  draw();
  renderFindings(rows);
}

function findingCard(kicker, heading, explanation, entries, signed = false) {
  const card = node('article', 'panel finding-card');
  card.append(node('p', 'eyebrow', kicker), node('h3', '', heading), node('p', 'finding-description', explanation));
  const plot = node('div', 'finding-plot'); card.append(plot);
  barChart(plot, entries, { title: heading, formatter: (value) => formatPercent(value, signed), signed });
  return card;
}

function renderFindings(rows) {
  const result = calculateFindings(rows);
  const target = byId('findings'); target.replaceChildren();
  const topPerformance = result.performance[0];
  target.append(findingCard('01 / CLOSING PERFORMANCE', topPerformance ? `${topPerformance.ticker} leads closing-price performance` : 'Closing performance needs more history', topPerformance ? `${company(topPerformance.ticker)} moved from ${formatMoney(topPerformance.first)} on ${formatDate(topPerformance.firstDate)} to ${formatMoney(topPerformance.last)} on ${formatDate(topPerformance.lastDate)}: ${formatPercent(topPerformance.value, true)}. Method: (last distinct-session close − first distinct-session close) ÷ first close × 100; stocks need two valid sessions and a positive first close.` : 'At least two distinct trading sessions with valid positive closing prices are required. No performance result is inferred from one observation.', result.performance.slice(0, 6), true));
  const topVolatility = result.volatility[0];
  target.append(findingCard('02 / RETURN VOLATILITY', topVolatility ? `${topVolatility.ticker} has the most variable returns` : 'Volatility needs more history', topVolatility ? `${company(topVolatility.ticker)} has a ${formatPercent(topVolatility.value)} sample standard deviation across ${topVolatility.count} consecutive-session closing returns. Each return is (current close − prior close) ÷ prior close × 100; sample standard deviation uses the mean and divides squared deviations by n − 1. At least two returns are required.` : 'At least three distinct trading sessions with two valid consecutive closing returns per stock are needed. Repeated weekend bars do not add returns.', result.volatility.slice(0, 6)));
  const topVolume = result.volume[0];
  target.append(findingCard('03 / VOLUME SHIFT', topVolume ? `${topVolume.ticker} has the largest volume shift` : 'Volume shift needs more history', topVolume ? `Volume changed from ${formatNumber(topVolume.first)} to ${formatNumber(topVolume.last)} shares between ${formatDate(topVolume.firstDate)} and ${formatDate(topVolume.lastDate)}: ${formatPercent(topVolume.value, true)}. Method: (later volume − earlier volume) ÷ earlier volume × 100 for consecutive distinct trading sessions; rank by absolute percentage change, requiring positive earlier volume.` : 'Two distinct trading sessions with valid volume and a positive earlier volume are required. No change is inferred from one session.', result.volume.slice(0, 6), true));
  const topRange = result.range[0];
  target.append(findingCard('04 / INTRADAY RANGE', topRange ? `${topRange.ticker} has the widest typical daily range` : 'Intraday range needs valid bars', topRange ? `${company(topRange.ticker)} averages ${formatPercent(topRange.value)} across ${topRange.count} distinct trading sessions. For each session, range = (high − low) ÷ open × 100; the finding is the arithmetic mean of valid session ranges with opening price above zero.` : 'A valid source trading session with positive open, high, and low is required before an intraday range can be calculated.', result.range.slice(0, 6)));
}

async function main() {
  const demo = isDemoMode();
  setupShell(demo);
  set('data-message', 'Loading market observations…');
  try {
    const dataset = await loadDataset(demo);
    byId('data-message').replaceChildren();
    const page = document.body.dataset.page;
    if (page === 'today') renderToday(dataset);
    else if (page === 'browse') renderBrowse(dataset);
    else if (page === 'trends') renderTrends(dataset);
  } catch (error) { showError(error.message || 'The data files could not be read.', demo); }
}

main();
