/** Read-only descriptions of a stored AnalysisResult (spec, sources, caveats, trace). */
import type {
  AnalysisResult,
  AnalysisSpec,
  DatasetMeta,
  Expr,
  Metric,
  ResultStatus,
  ResultTable,
  SpecFilter,
  TimeGrain,
} from '../api/types';
import { formatTime } from './format';

export function describeExpr(expr: Expr | null | undefined, single: boolean, parent = 0): string {
  if (!expr) return '*';
  if (expr.kind === 'col') return single ? expr.ref.column : `${expr.ref.dataset}.${expr.ref.column}`;
  if (expr.kind === 'num') return String(expr.value);
  const prec = expr.op === '+' || expr.op === '-' ? 1 : 2;
  const text = `${describeExpr(expr.left, single, prec)} ${expr.op} ${describeExpr(expr.right, single, prec + (expr.op === '-' || expr.op === '/' ? 1 : 0))}`;
  return prec < parent ? `(${text})` : text;
}

export function datasetNamesOf(spec: AnalysisSpec): string[] {
  return [...new Set([spec.base_dataset, ...spec.joins.flatMap((j) => [j.left.dataset, j.right.dataset])])];
}

export function describeMetric(metric: Metric, single: boolean): string {
  const inner = describeExpr(metric.expr ?? null, single);
  return `${metric.agg}(${inner})`;
}

const OPS: Record<string, string> = { eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤' };

export function describeFilter(f: SpecFilter): string {
  const col = f.column.column;
  const v = f.value;
  switch (f.op) {
    case 'in':
      return `${col} in ${Array.isArray(v) ? v.join(', ') : String(v)}`;
    case 'not_in':
      return `${col} not in ${Array.isArray(v) ? v.join(', ') : String(v)}`;
    case 'between':
      return Array.isArray(v) ? `${col} between ${v[0]} and ${v[1]}` : `${col} between`;
    case 'is_null':
      return `${col} is empty`;
    case 'not_null':
      return `${col} is not empty`;
    default:
      return `${col} ${OPS[f.op] ?? f.op} ${String(v)}`;
  }
}

/** Date filters on the bucketed time column (or any column named like a date). */
export function periodText(spec: AnalysisSpec): string {
  const timeCol = spec.time_bucket?.column.column;
  const dateFilters = spec.filters.filter(
    (f) => f.column.column === timeCol || /date|time|month|year|day/i.test(f.column.column),
  );
  if (!dateFilters.length) return 'No date filter';
  return dateFilters.map(describeFilter).join(', ');
}

export interface ContextItem {
  k: string;
  v: string;
}

export function contextItems(spec: AnalysisSpec | null | undefined): ContextItem[] {
  if (!spec) return [];
  const single = datasetNamesOf(spec).length === 1;
  const groups = [
    ...(spec.time_bucket ? [`${spec.time_bucket.column.column} by ${spec.time_bucket.grain}`] : []),
    ...spec.dimensions.map((d) => d.column),
  ];
  return [
    { k: 'Data', v: datasetNamesOf(spec).join(' + ') },
    { k: 'Metric', v: spec.metrics.map((m) => describeMetric(m, single)).join(', ') },
    { k: 'Filters', v: spec.filters.length ? spec.filters.map(describeFilter).join(', ') : 'None' },
    { k: 'Period', v: periodText(spec) },
    { k: 'Grouped by', v: groups.length ? groups.join(', ') : 'None' },
  ];
}

export interface SourceVersion {
  id: string;
  name: string;
  version: number;
  missing: boolean;
}

export function sourceVersions(result: AnalysisResult, datasets: DatasetMeta[]): SourceVersion[] {
  return Object.entries(result.meta?.dataset_versions ?? {}).map(([id, version]) => {
    const found = datasets.find((d) => d.id === id);
    return { id, name: found?.name ?? 'deleted dataset', version, missing: !found };
  });
}

/** Live comparison of the result's source versions with the current datasets. */
export function deriveStatus(result: AnalysisResult, datasets: DatasetMeta[] | null, apiStatus?: ResultStatus): ResultStatus {
  if (!datasets) return apiStatus ?? 'current';
  let stale = false;
  for (const [id, version] of Object.entries(result.meta?.dataset_versions ?? {})) {
    const found = datasets.find((d) => d.id === id);
    if (!found) return 'missing';
    if (found.version !== version) stale = true;
  }
  return stale ? 'stale' : 'current';
}

export function staleLabel(status: ResultStatus, sources: SourceVersion[]): string {
  if (status === 'missing') return 'Source deleted';
  if (status === 'stale') {
    const names = sources.filter((s) => !s.missing).map((s) => s.name);
    return names.length ? `Based on an earlier version of ${names.join(', ')}` : 'Based on an earlier version of the data';
  }
  return '';
}

export function detailItems(result: AnalysisResult, datasets: DatasetMeta[]): ContextItem[] {
  const spec = result.spec;
  const meta = result.meta;
  const single = spec ? datasetNamesOf(spec).length === 1 : true;
  const items: ContextItem[] = [];
  const sources = sourceVersions(result, datasets);
  items.push({
    k: 'Data sources',
    v: sources.length ? sources.map((s) => `${s.name} (v${s.version})`).join(', ') : (spec ? datasetNamesOf(spec).join(', ') : '—'),
  });
  if (spec) {
    items.push({ k: 'Metric definition', v: spec.metrics.map((m) => `${m.name} = ${describeMetric(m, single)}`).join('; ') });
    items.push({ k: 'Filters', v: spec.filters.length ? spec.filters.map(describeFilter).join(', ') : 'None' });
    items.push({
      k: 'Grouping',
      v:
        [...(spec.time_bucket ? [`${spec.time_bucket.column.column} by ${spec.time_bucket.grain}`] : []), ...spec.dimensions.map((d) => d.column)].join(', ') ||
        'None',
    });
    if (spec.joins.length) {
      items.push({
        k: 'Joins',
        v: spec.joins.map((j) => `${j.left.dataset}.${j.left.column} → ${j.right.dataset}.${j.right.column}${j.pre_aggregate ? ' (pre-aggregated)' : ''}`).join('; '),
      });
    }
    items.push({ k: 'Sort and limit', v: `${spec.sort.map((s) => `${s.by} ${s.descending ? 'desc' : 'asc'}`).join(', ') || 'default order'}${spec.limit ? ` · limit ${spec.limit}` : ''}` });
  }
  if (meta) {
    items.push({
      k: 'Calculation',
      v: `Computed in DuckDB: ${meta.row_count.toLocaleString('en-GB')} row${meta.row_count === 1 ? '' : 's'} in ${meta.elapsed_ms} ms${meta.truncated ? ' · result truncated at the row cap' : ''}`,
    });
  }
  items.push({
    k: 'Validation',
    v:
      result.outcome === 'ok'
        ? `${result.claims.length} claim${result.claims.length === 1 ? '' : 's'} re-derived from the result table`
        : result.outcome === 'narrative_unverified'
          ? 'The written summary failed validation and was withheld'
          : `Outcome: ${result.outcome.replace(/_/g, ' ')}`,
  });
  return items;
}

export function formatParam(value: unknown): string {
  if (typeof value === 'string') return `'${value}'`;
  if (value === null || value === undefined) return 'NULL';
  return String(value);
}

const clip = (text: string, max = 90) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

/** One line of the stored tool trace: the tool name, then its fields with long values clipped. */
export function traceLine(entry: Record<string, unknown>): { label: string; detail: string } {
  const tool = typeof entry.tool === 'string' ? entry.tool : Object.keys(entry)[0] ?? 'step';
  const show = (v: unknown): string => {
    if (typeof v === 'string') return clip(v);
    if (v && typeof v === 'object') {
      return `{${Object.entries(v as Record<string, unknown>)
        .map(([k, x]) => `${k}: ${typeof x === 'string' ? clip(x, 40) : JSON.stringify(x)}`)
        .join(', ')}}`;
    }
    return String(v);
  };
  const detail = Object.entries(entry)
    .filter(([k]) => k !== 'tool')
    .map(([k, v]) => `${k}=${show(v)}`)
    .join(' · ');
  return { label: tool, detail };
}

/** "Jan 2026 · region West" for a flagged row: time columns are formatted, the rest are "column value". */
export function entityLabel(rowKey: Record<string, unknown>, table?: ResultTable | null, grain?: TimeGrain | null): string {
  const roles = new Map((table?.columns ?? []).map((c) => [c.name, c.role]));
  const time: string[] = [];
  const rest: string[] = [];
  for (const [k, v] of Object.entries(rowKey)) {
    if (roles.get(k) === 'time') time.push(formatTime(v as string, grain));
    else rest.push(`${k} ${String(v)}`);
  }
  return [...time, ...rest].join(' · ') || 'Observation';
}

export function countChecks(result: AnalysisResult): number {
  return result.trace.filter((t) => typeof t.tool === 'string' && 'step' in t).length;
}
