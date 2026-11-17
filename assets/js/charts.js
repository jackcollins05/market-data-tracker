/* Accessible, dependency-free SVG charts. Source strings are inserted as text only. */
const SVG_NS = 'http://www.w3.org/2000/svg';
export const COLORS = ['#52c7a1', '#85b8ef', '#f3b770', '#c9a6ee', '#ef8e9d', '#d4d97d'];

function svgNode(tag, attributes = {}, text = null) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text !== null) element.textContent = String(text);
  return element;
}

function prepare(container, title, description, width, height) {
  container.replaceChildren();
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': title, preserveAspectRatio: 'xMidYMid meet' });
  svg.append(svgNode('title', {}, title), svgNode('desc', {}, description));
  container.append(svg);
  return svg;
}

function empty(container, message) {
  container.replaceChildren();
  const paragraph = document.createElement('p');
  paragraph.className = 'chart-empty';
  paragraph.textContent = message;
  container.append(paragraph);
}

export function lineChart(container, series, { title, unit, valueLabel }) {
  const usable = series.map((item) => ({ ...item, points: item.points.filter((point) => Number.isFinite(point.value)) }))
    .filter((item) => item.points.length);
  if (!usable.length) { empty(container, 'No valid historical values for this selection.'); return; }
  const dates = [...new Set(usable.flatMap((item) => item.points.map((point) => point.date)))].sort();
  const values = usable.flatMap((item) => item.points.map((point) => point.value));
  const min = Math.min(...values), max = Math.max(...values);
  const low = min === max ? Math.max(0, min * 0.9) : Math.max(0, min - (max - min) * 0.12);
  const high = min === max ? max + (max === 0 ? 1 : max * 0.1) : max + (max - min) * 0.12;
  const width = 900, height = 290, left = 84, right = 24, top = 20, bottom = 55;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = (day) => left + (dates.length === 1 ? plotWidth / 2 : dates.indexOf(day) / (dates.length - 1) * plotWidth);
  const y = (value) => top + (high - value) / (high - low || 1) * plotHeight;
  const svg = prepare(container, title, `${title}. ${usable.map((item) => `${item.ticker}: ${item.points.length} trading sessions`).join('; ')}. Values in ${unit}.`, width, height);
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = low + (high - low) * tick / 4;
    const yy = y(value);
    svg.append(svgNode('line', { x1: left, y1: yy, x2: width - right, y2: yy, class: 'chart-grid' }));
    svg.append(svgNode('text', { x: left - 12, y: yy + 4, 'text-anchor': 'end', class: 'chart-axis' }, valueLabel(value)));
  }
  const dateIndices = [...new Set([0, Math.floor((dates.length - 1) / 2), dates.length - 1])];
  for (const index of dateIndices) svg.append(svgNode('text', { x: x(dates[index]), y: height - 22, 'text-anchor': index === 0 && dates.length > 1 ? 'start' : index === dates.length - 1 && dates.length > 1 ? 'end' : 'middle', class: 'chart-axis' }, dates[index]));
  usable.forEach((item, index) => {
    const color = COLORS[index % COLORS.length];
    const sorted = [...item.points].sort((a, b) => a.date.localeCompare(b.date));
    if (sorted.length > 1) svg.append(svgNode('polyline', { points: sorted.map((point) => `${x(point.date)},${y(point.value)}`).join(' '), fill: 'none', stroke: color, 'stroke-width': 2.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    for (const point of sorted) {
      const circle = svgNode('circle', { cx: x(point.date), cy: y(point.value), r: 4, fill: color });
      circle.append(svgNode('title', {}, `${item.ticker} · ${point.date} · ${valueLabel(point.value)} ${unit}`));
      svg.append(circle);
    }
  });
  const legend = document.createElement('div');
  legend.className = 'chart-legend';
  usable.forEach((item, index) => {
    const label = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.style.backgroundColor = COLORS[index % COLORS.length];
    label.append(swatch, document.createTextNode(item.ticker));
    legend.append(label);
  });
  container.append(legend);
}

export function barChart(container, entries, { title, formatter, signed = false }) {
  const values = entries.filter((entry) => Number.isFinite(entry.value)).slice(0, 7);
  if (!values.length) { empty(container, 'More distinct trading sessions are needed for this finding.'); return; }
  const width = 900, rowHeight = 43, height = 42 + values.length * rowHeight, labelWidth = 76, endMargin = 98;
  const plotWidth = width - labelWidth - endMargin;
  const maximum = Math.max(...values.map((entry) => Math.abs(entry.value)), 0.01);
  const zeroX = signed ? labelWidth + plotWidth / 2 : labelWidth;
  const svg = prepare(container, title, `${title}. ${values.map((entry) => `${entry.ticker}: ${formatter(entry.value)}`).join('; ')}.`, width, height);
  svg.append(svgNode('line', { x1: zeroX, y1: 18, x2: zeroX, y2: height - 10, class: 'chart-zero' }));
  values.forEach((entry, index) => {
    const y = 25 + index * rowHeight;
    const available = signed ? plotWidth / 2 - 8 : plotWidth - 8;
    const length = Math.max(2, Math.abs(entry.value) / maximum * available);
    const x = entry.value < 0 && signed ? zeroX - length : zeroX;
    const fill = entry.value < 0 ? '#d87982' : '#52c7a1';
    svg.append(svgNode('text', { x: 8, y: y + 15, class: 'chart-label' }, entry.ticker));
    svg.append(svgNode('rect', { x, y, width: length, height: 20, rx: 4, fill, opacity: 0.9 }));
    svg.append(svgNode('text', { x: width - 8, y: y + 15, 'text-anchor': 'end', class: 'chart-label' }, formatter(entry.value)));
  });
}
