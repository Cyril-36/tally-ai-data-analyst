import { describe, expect, it } from 'vitest';
import { ORDERS, okResult } from '../test/fixtures';
import { currencyChanges, deriveStatus, kpiLabel, plainCalculation, sourceVersions, staleLabel } from './resultView';

const calculated = {
  ...okResult,
  meta: { ...okResult.meta!, dataset_versions: { [ORDERS.id]: ORDERS.version }, source_currencies: { [ORDERS.id]: 'GBP' } },
};

describe('result staleness', () => {
  it('is current while version and declared currency are unchanged', () => {
    expect(deriveStatus(calculated, [ORDERS])).toBe('current');
  });

  it('is out of date after the currency declaration changes, and says so', () => {
    const now = [{ ...ORDERS, currency: 'USD' as const }];
    expect(deriveStatus(calculated, now)).toBe('stale');
    const label = staleLabel('stale', sourceVersions(calculated, now), currencyChanges(calculated, now));
    expect(label).toBe('orders currency changed from GBP to USD');
  });

  it('a renamed dataset is not a currency change', () => {
    expect(deriveStatus(calculated, [{ ...ORDERS, name: 'sales' }])).toBe('current');
  });

  it('a new file version is still reported as a replacement', () => {
    const now = [{ ...ORDERS, version: ORDERS.version + 1 }];
    expect(staleLabel('stale', sourceVersions(calculated, now), currencyChanges(calculated, now))).toBe(
      'Based on an earlier version of orders',
    );
  });
});

describe('plain-language calculation', () => {
  it('says whether rows or unique entities are counted, with grouping and filters', () => {
    const col = (column: string) => ({ kind: 'col' as const, ref: { dataset: 'orders', column } });
    expect(
      plainCalculation({
        base_dataset: 'orders',
        metrics: [{ name: 'orders', agg: 'count_distinct', expr: col('order_id') }],
        dimensions: [],
        time_bucket: { column: { dataset: 'orders', column: 'order_date' }, grain: 'month' },
        filters: [{ column: { dataset: 'orders', column: 'region' }, op: 'eq', value: 'West' }],
        joins: [],
        sort: [],
        limit: null,
      }),
    ).toBe('Counts unique order_id values per month, where region = West.');
    expect(
      plainCalculation({
        base_dataset: 'orders',
        metrics: [{ name: 'rows', agg: 'count', expr: null }, { name: 'avg_price', agg: 'avg', expr: col('unit_price') }],
        dimensions: [{ dataset: 'orders', column: 'region' }],
        time_bucket: null,
        filters: [],
        joins: [],
        sort: [],
        limit: null,
      }),
    ).toBe('Counts rows; averages unit_price per region.');
  });
});

describe('headline figure labels', () => {
  const base = { metric: 'revenue', unit: 'GBP', rank: null, rank_of: null, direction: null, compared_to: null, periods_covered: null, period: null };
  const top = { ...base, id: 'c1', kind: 'rank' as const, entity: { region: 'West' }, value: 100, rank: 1, rank_of: 5, direction: 'highest', source: { type: 'cell' } };
  const runner = { ...base, id: 'c2', kind: 'value' as const, entity: { region: 'North' }, value: 80, source: { type: 'cell' } };
  const gap = { ...base, id: 'c3', kind: 'comparison' as const, entity: { region: 'West' }, value: 20, compared_to: 'c2', source: { type: 'calc', op: 'difference', inputs: ['c1', 'c2'] } };
  const share = { ...base, id: 'c4', kind: 'share' as const, unit: '%', entity: { region: 'West' }, value: 25, source: { type: 'calc', op: 'share_of_total', inputs: ['c1'] } };
  const change = { ...base, id: 'c5', kind: 'comparison' as const, unit: '%', entity: { region: 'East' }, value: -22, period: '2026-09-01', source: { type: 'calc', op: 'pct_change', inputs: [] } };
  const all = [top, runner, gap, share, change];
  it('names what each figure is', () => {
    expect(kpiLabel(top, all)).toBe('revenue · West · highest of 5');
    expect(kpiLabel(gap, all)).toBe('revenue · West ahead of North');
    expect(kpiLabel(share, all)).toBe('West · share of total revenue');
    expect(kpiLabel(change, all, 'month')).toBe('revenue · East · change vs previous · Sep 2026');
  });
});
