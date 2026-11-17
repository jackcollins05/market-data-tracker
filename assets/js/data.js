import { normalizeHistory, parseCSV, validateStatus } from './core.js';

export const DEMO_ROOT = 'tests/fixtures/site_demo/';
export const LIVE_ROOT = 'data/';

export function isDemoMode() {
  return new URLSearchParams(window.location.search).get('demo') === '1';
}

export function pageHref(page, demo = isDemoMode()) {
  return `${page}.html${demo ? '?demo=1' : ''}`;
}

export function responseError(response, label, demo) {
  if (response.status === 404) return demo
    ? `Synthetic demonstration ${label} is missing.`
    : 'Production data has not been collected yet.';
  return `${label} could not be loaded (HTTP ${response.status}).`;
}

export function parseDatasetTexts(historyText, statusText, demo = false) {
  if (/^\s*(?:<!doctype\s+html|<html\b)/i.test(historyText) || /^\s*(?:<!doctype\s+html|<html\b)/i.test(statusText)) {
    throw new Error(demo ? 'Synthetic demonstration fixtures are missing; the server returned HTML instead.' : 'Production data has not been collected yet.');
  }
  let parsedRows;
  try { parsedRows = parseCSV(historyText); }
  catch (error) {
    throw new Error(error.message.includes('columns do not match')
      ? 'History CSV schema does not match the backend contract.'
      : 'History CSV structure is invalid.');
  }
  if (!parsedRows.length) throw new Error('History CSV is valid but contains no observation rows.');
  const rows = normalizeHistory(historyText);
  if (!rows.length) throw new Error('History CSV contains no usable observation rows.');
  let parsedStatus;
  try { parsedStatus = JSON.parse(statusText); }
  catch { throw new Error('Status JSON is invalid.'); }
  let status;
  try { status = validateStatus(parsedStatus); }
  catch { throw new Error('Status JSON schema does not match the backend contract.'); }
  if (demo && status.demo !== true) throw new Error('The demonstration status file is missing its synthetic-data marker.');
  return { rows, status, demo };
}

export async function loadDataset(demo = isDemoMode(), fetcher = fetch) {
  const root = demo ? DEMO_ROOT : LIVE_ROOT;
  let historyResponse, statusResponse;
  try {
    [historyResponse, statusResponse] = await Promise.all([
      fetcher(`${root}history.csv`, { cache: 'no-store' }),
      fetcher(`${root}status.json`, { cache: 'no-store' }),
    ]);
  } catch {
    throw new Error('Website data files could not be reached. Check the local server or connection.');
  }
  if (!historyResponse.ok) throw new Error(responseError(historyResponse, 'History CSV', demo));
  if (!statusResponse.ok) throw new Error(responseError(statusResponse, 'Status JSON', demo));
  const historyType = historyResponse.headers && historyResponse.headers.get('content-type') || '';
  const statusType = statusResponse.headers && statusResponse.headers.get('content-type') || '';
  if (/text\/html/i.test(historyType) || /text\/html/i.test(statusType)) {
    throw new Error(demo ? 'Synthetic demonstration fixtures are missing; the server returned HTML instead.' : 'Production data has not been collected yet.');
  }
  let historyText, statusText;
  try { [historyText, statusText] = await Promise.all([historyResponse.text(), statusResponse.text()]); }
  catch { throw new Error('Website data files could not be read.'); }
  return parseDatasetTexts(historyText, statusText, demo);
}
