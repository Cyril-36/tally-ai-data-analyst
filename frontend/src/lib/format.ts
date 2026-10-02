/**
 * Presentation formatting only. Nothing in here recalculates an analysis value: it turns
 * numbers and dates that came from the API into text.
 */
import type { AnalysisResult, Cell, ResultColumn, TimeGrain } from '../api/types';

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function isCurrencyCode(unit: string | null | undefined): boolean {
  if (!unit || !/^[A-Z]{3}$/.test(unit)) return false;
  try {
    new Intl.NumberFormat('en', { style: 'currency', currency: unit });
    return true;
  } catch {
    return false;
  }
}

export function currencySymbol(code: string): string {
  const part = new Intl.NumberFormat('en-GB', { style: 'currency', currency: code, currencyDisplay: 'narrowSymbol' })
    .formatToParts(0)
    .find((p) => p.type === 'currency');
  return part ? part.value : code;
}

/** The currency of a result column comes from the result's own claims (`unit`), never hard-coded. */
export function currencyFor(result: Pick<AnalysisResult, 'claims'>, column: string): string | null {
  for (const claim of result.claims) {
    if (claim.metric === column && claim.unit && isCurrencyCode(claim.unit)) return claim.unit;
  }
  return null;
}

/** `decimals` fixes the fraction digits so every cell of a column lines up. */
export function formatNumber(value: number, currency?: string | null, decimals?: number): string {
  if (currency) {
    return new Intl.NumberFormat('en-GB', { style: 'currency', currency, currencyDisplay: 'narrowSymbol' }).format(value);
  }
  if (decimals !== undefined) {
    return new Intl.NumberFormat('en-GB', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(value);
  }
  const abs = Math.abs(value);
  return new Intl.NumberFormat('en-GB', { maximumFractionDigits: Number.isInteger(value) ? 0 : abs < 1 ? 4 : 2 }).format(value);
}

/** A claim value with its unit ("GBP" -> £, "%" -> 12.5%, anything else -> "value unit"). */
export function formatWithUnit(value: number, unit?: string | null): string {
  if (unit && isCurrencyCode(unit)) return formatNumber(value, unit);
  if (unit === '%') return `${formatNumber(value)}%`;
  return unit ? `${formatNumber(value)} ${unit}` : formatNumber(value);
}

const ISO = /^(\d{4})-(\d{2})(?:-(\d{2}))?/;

export function formatTime(value: Cell | undefined, grain?: TimeGrain | null): string {
  if (value === null || value === undefined || value === '') return '—';
  const text = String(value);
  const m = ISO.exec(text);
  if (!m) return text;
  const [, y, mo, d] = m;
  const month = MON[Number(mo) - 1] ?? mo;
  switch (grain) {
    case 'year':
      return y;
    case 'quarter':
      return `Q${Math.floor((Number(mo) - 1) / 3) + 1} ${y}`;
    case 'month':
      return `${month} ${y}`;
    case 'week':
      return d ? `Week of ${month} ${Number(d)}, ${y}` : `${month} ${y}`;
    default:
      return d ? `${month} ${Number(d)}, ${y}` : `${month} ${y}`;
  }
}

export function formatCell(
  value: Cell | undefined,
  column: ResultColumn,
  opts: { currency?: string | null; grain?: TimeGrain | null; decimals?: number },
): string {
  if (value === null || value === undefined || value === '') return '—';
  if (column.role === 'time') return formatTime(value, opts.grain);
  if (typeof value === 'number') return formatNumber(value, column.role === 'metric' ? opts.currency : null, column.role === 'metric' ? opts.decimals : undefined);
  return String(value);
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function ago(iso: string | number | undefined, now: number = Date.now()): string {
  if (iso === undefined) return '';
  const ts = typeof iso === 'number' ? iso : Date.parse(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(ts)) return '';
  const s = Math.max(0, (now - ts) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function clock(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  return new Date(ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

export function shortDate(iso: string): string {
  const ts = Date.parse(iso);
  return Number.isNaN(ts) ? '' : new Date(ts).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function shortId(id: string): string {
  return `R-${id.slice(0, 6).toUpperCase()}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString('en-GB')} ${n === 1 ? one : many}`;
}

export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}
