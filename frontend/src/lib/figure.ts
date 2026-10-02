/**
 * Builds a Plotly figure from the stored result table plus its ChartSpec (encoding only).
 * No value is computed here: y is always a table cell. Styling follows the Tally prototype.
 */
import type { AnalysisResult, AnomalyFlag, Cell, ChartSpec, ChartType, ResultTable, TimeGrain } from '../api/types';
import { currencyFor, currencySymbol, formatNumber, formatTime } from './format';

const G = '#1F5C4A';
const G2 = '#9DBBAE';
const FLAG = '#B8401A';
const INK = '#1B1B18';
const MUTE = '#57554D';
const PALETTE = [G, G2, '#5E8C7C', '#C9C2B3', '#D9A441', MUTE];

export interface Figure {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
  config: Record<string, unknown>;
}

export interface FigureInput {
  table: ResultTable;
  chart: ChartSpec;
  result: Pick<AnalysisResult, 'claims'>;
  grain?: TimeGrain | null;
  flags?: AnomalyFlag[];
  mini?: boolean;
}

function indexOfColumn(table: ResultTable, name: string): number {
  return table.columns.findIndex((c) => c.name === name);
}

const TICK_FORMAT: Record<TimeGrain, string> = {
  day: '%b %-d, %Y',
  week: '%b %-d, %Y',
  month: '%b %Y',
  quarter: '%b %Y',
  year: '%Y',
};

function matchesKey(table: ResultTable, row: Cell[], key: Record<string, Cell>): boolean {
  return Object.entries(key).every(([name, value]) => {
    const i = indexOfColumn(table, name);
    return i >= 0 && row[i] === value;
  });
}

export function buildFigure({ table, chart, result, grain, flags, mini = false }: FigureInput): Figure {
  const xi = indexOfColumn(table, chart.x);
  const xCol = table.columns[xi];
  const timeX = xCol?.role === 'time';
  const si = chart.series ? indexOfColumn(table, chart.series) : -1;
  const currencies = chart.y.map((y) => currencyFor(result, y));
  const sharedCurrency = currencies.every((c) => c === currencies[0]) ? currencies[0] : null;
  const fmtY = (v: unknown, metric: string) => (typeof v === 'number' ? formatNumber(v, currencyFor(result, metric)) : '—');
  const xLabel = (v: Cell) => (timeX ? formatTime(v, grain) : v === null ? '—' : String(v));

  const rows = table.rows;
  const type = chart.type;
  let data: Figure['data'];

  if (type === 'pie') {
    const yi = indexOfColumn(table, chart.y[0]);
    data = [
      {
        type: 'pie',
        labels: rows.map((r) => xLabel(r[xi])),
        values: rows.map((r) => r[yi]),
        hole: 0.55,
        sort: false,
        marker: { colors: PALETTE, line: { color: '#FFFFFF', width: 2 } },
        textinfo: 'percent',
        textfont: { color: '#FFFFFF' },
        customdata: rows.map((r) => fmtY(r[yi], chart.y[0])),
        hovertemplate: '<b>%{label}</b><br>%{customdata} · %{percent}<extra></extra>',
      },
    ];
  } else if (type === 'histogram') {
    data = [
      {
        type: 'histogram',
        x: rows.map((r) => r[xi]),
        name: chart.x,
        marker: { color: G },
      },
    ];
  } else {
    const groups = si >= 0 ? [...new Set(rows.map((r) => r[si]))] : [null];
    const traces: Figure['data'] = [];
    chart.y.forEach((metric, mi) => {
      const yi = indexOfColumn(table, metric);
      groups.forEach((group, gi) => {
        let points = rows.filter((r) => si < 0 || r[si] === group);
        if ((type === 'line' || type === 'area') && timeX) {
          points = [...points].sort((a, b) => String(a[xi]).localeCompare(String(b[xi])));
        }
        const name = si >= 0 ? `${metric} (${String(group)})` : metric;
        const color = PALETTE[(chart.y.length > 1 ? mi : gi) % PALETTE.length] ?? G;
        const single = chart.y.length * groups.length === 1;
        traces.push({
          type: type === 'bar' ? 'bar' : 'scatter',
          mode: type === 'scatter' ? 'markers' : 'lines+markers',
          name,
          x: points.map((r) => (timeX ? r[xi] : xLabel(r[xi]))),
          y: points.map((r) => r[yi]),
          fill: type === 'area' ? 'tozeroy' : undefined,
          customdata: points.map((r) => fmtY(r[yi], metric)),
          marker: { color: single ? G : color, size: type === 'line' || type === 'area' ? 6 : 9 },
          line: { color: single ? G : color, width: 2.5 },
          hovertemplate: `<b>%{x}</b><br>${name}: %{customdata}<extra></extra>`,
        });
      });
    });
    if (flags?.length) {
      const yi = indexOfColumn(table, flags[0].metric);
      const hits = flags
        .map((f) => ({ f, row: rows.find((r) => matchesKey(table, r, f.row_key)) }))
        .filter((h): h is { f: AnomalyFlag; row: Cell[] } => !!h.row);
      traces.push({
        type: 'scatter',
        mode: 'markers',
        name: 'Flagged',
        x: hits.map((h) => h.row[xi]),
        y: hits.map((h) => h.row[yi]),
        marker: { color: FLAG, size: mini ? 8 : 12, line: { color: '#FFFFFF', width: 2 } },
        customdata: hits.map((h) => `${fmtY(h.row[yi], flags[0].metric)} · robust z ${formatNumber(h.f.robust_z)}`),
        hovertemplate: '<b>%{x}</b><br>%{customdata}<extra>Flagged</extra>',
      });
    }
    data = traces;
  }

  const yAxis: Record<string, unknown> = {
    gridcolor: '#ECE8E0',
    zerolinecolor: '#CEC9BE',
    tickfont: { color: MUTE },
    automargin: true,
    tickformat: '~s',
  };
  if (sharedCurrency) yAxis.tickprefix = currencySymbol(sharedCurrency);
  if (!mini) yAxis.title = { text: chart.y.join(', '), font: { size: 11.5, color: MUTE } };

  const xAxis: Record<string, unknown> = { tickfont: { color: MUTE }, automargin: true, gridcolor: '#ECE8E0', showgrid: !!flags?.length };
  if (!mini && type !== 'pie') xAxis.title = { text: chart.x, font: { size: 11.5, color: MUTE } };
  if (timeX && type !== 'pie' && type !== 'histogram') {
    xAxis.type = 'date';
    if (grain) xAxis.tickformat = TICK_FORMAT[grain];
  }

  const multi = chart.y.length > 1 || si >= 0;
  const layout: Record<string, unknown> = {
    autosize: true,
    margin: mini ? { l: 44, r: 8, t: 8, b: 30 } : { l: 64, r: 16, t: 44, b: 56 },
    font: { family: 'Geist Variable, Geist, system-ui, sans-serif', size: mini ? 11 : 12, color: INK },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    xaxis: xAxis,
    yaxis: yAxis,
    showlegend: (type === 'pie' || multi || !!flags?.length) && !mini,
    legend: { orientation: 'h', y: -0.25, x: 0 },
    hoverlabel: { bgcolor: INK, bordercolor: INK, font: { color: '#F3F1EC', family: 'Geist Variable, Geist', size: 12 } },
    bargap: 0.35,
  };
  if (!mini) layout.title = { text: chart.title, x: 0, xanchor: 'left', font: { size: 14, color: INK } };

  return {
    data,
    layout,
    config: {
      responsive: true,
      displaylogo: false,
      // The prototype shows the toolbar on full charts; on phones it covers the title.
      displayModeBar: !mini && !(typeof window !== 'undefined' && window.matchMedia?.('(max-width: 640px)').matches),
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
    },
  };
}

/** What the UI can offer for a result table. Mirrors the shape rules the backend enforces; the
 * backend remains the authority and any refusal is shown to the user. */
export function candidateChartTypes(table: ResultTable, chart: ChartSpec): ChartType[] {
  const col = table.columns.find((c) => c.name === chart.x);
  if (!col) return [chart.type];
  const out: ChartType[] = [];
  const xi = table.columns.indexOf(col);
  const yi = table.columns.findIndex((c) => c.name === chart.y[0]);
  const numericX = table.rows.length > 0 && table.rows.every((r) => r[xi] === null || (typeof r[xi] === 'number' && Number.isFinite(r[xi] as number)));
  if (col.role !== 'metric') out.push('bar');
  if (col.role === 'time') out.push('line', 'area');
  const pieOk =
    col.role !== 'metric' &&
    chart.y.length === 1 &&
    !chart.series &&
    yi >= 0 &&
    table.rows.every((r) => typeof r[yi] === 'number' && (r[yi] as number) >= 0) &&
    table.rows.some((r) => (r[yi] as number) > 0);
  if (pieOk) out.push('pie');
  if (numericX) out.push('scatter', 'histogram');
  if (!out.includes(chart.type)) out.unshift(chart.type);
  return out;
}

/** Plain-text alternative to the chart, one line per plotted point. */
export function chartTableAlternative(table: ResultTable, chart: ChartSpec, max = 60): { headers: string[]; rows: Cell[][] } {
  const names = [chart.x, ...(chart.series ? [chart.series] : []), ...chart.y];
  const idx = names.map((n) => indexOfColumn(table, n)).filter((i) => i >= 0);
  return {
    headers: idx.map((i) => table.columns[i].name),
    rows: table.rows.slice(0, max).map((r) => idx.map((i) => r[i])),
  };
}
