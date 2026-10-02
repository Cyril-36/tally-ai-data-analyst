import type { AnalysisResult, DatasetMeta } from '../api/types';

export const ORDERS: DatasetMeta = {
  id: 'ds-orders',
  name: 'orders',
  filename: 'orders.csv',
  version: 2,
  row_count: 4971,
  created_at: '2026-10-01T10:00:00+00:00',
  updated_at: '2026-10-01T10:00:00+00:00',
};

const base: AnalysisResult = {
  id: 'a3f9c21d77be4c8a9d3e5b1f0c2a4e68',
  owner_id: 'u1',
  conversation_id: 'c1',
  question: 'Which region generated the highest revenue?',
  outcome: 'ok',
  message: null,
  spec: {
    base_dataset: 'orders',
    metrics: [
      {
        name: 'revenue',
        agg: 'sum',
        expr: {
          kind: 'binop',
          op: '-',
          left: {
            kind: 'binop',
            op: '*',
            left: { kind: 'col', ref: { dataset: 'orders', column: 'quantity' } },
            right: { kind: 'col', ref: { dataset: 'orders', column: 'unit_price' } },
          },
          right: { kind: 'col', ref: { dataset: 'orders', column: 'discount_amount' } },
        },
      },
    ],
    dimensions: [{ dataset: 'orders', column: 'region' }],
    time_bucket: null,
    filters: [],
    joins: [],
    sort: [{ by: 'revenue', descending: true }],
    limit: null,
  },
  table: {
    columns: [
      { name: 'region', dtype: 'VARCHAR', role: 'dimension' },
      { name: 'revenue', dtype: 'DOUBLE', role: 'metric' },
    ],
    rows: [
      ['West', 135306.2],
      ['North', 109146.55],
      ['Central', 103225.51],
    ],
  },
  meta: {
    sql: 'SELECT "t0"."region" AS "region", SUM(("t0"."quantity" * "t0"."unit_price") - "t0"."discount_amount") AS "revenue" FROM "t0" GROUP BY 1 ORDER BY 2 DESC',
    params: ['West'],
    row_count: 3,
    truncated: false,
    elapsed_ms: 42,
    dataset_versions: { 'ds-orders': 2 },
    caveats: [{ code: 'derived_metric', message: "'revenue' is calculated as sum(quantity * unit_price - discount_amount).", count: null }],
  },
  chart: { type: 'bar', x: 'region', y: ['revenue'], series: null, title: 'Results by group' },
  claims: [
    {
      id: 'c0',
      kind: 'rank',
      metric: 'revenue',
      entity: { region: 'West' },
      period: null,
      value: 135306.2,
      unit: 'GBP',
      source: { type: 'cell', row_key: { region: 'West' }, column: 'revenue' },
      rank: 1,
      rank_of: 3,
      direction: 'highest',
    },
  ],
  answer_sentences: ['region=West had the highest revenue: 135,306.2 among 3 returned groups.', 'region=West: revenue was 135,306.2.'],
  anomalies: null,
  clarification: null,
  suggested_questions: ['Compare these groups.'],
  trace: [
    { tool: 'run_analysis', args: { spec_json: '{"base_dataset":"orders"}' }, step: 1 },
    { tool: 'run_analysis', ok: true, summary: 'Tool completed' },
    { tool: 'finish', args: { result_id: 'a3f9' }, step: 2 },
  ],
  created_at: '2026-10-02T09:15:00+00:00',
};

export const okResult: AnalysisResult = base;

export const unverifiedResult: AnalysisResult = {
  ...base,
  id: 'b7d1e0aa11cc4f2b8a90c3d4e5f60718',
  outcome: 'narrative_unverified',
  message: 'The written summary could not be validated. The calculated table is available.',
  claims: [],
  answer_sentences: [],
  suggested_questions: [],
};

export const clarifyResult: AnalysisResult = {
  ...base,
  id: 'c0ffee00c0ffee00c0ffee00c0ffee00',
  question: 'Which products are underperforming?',
  outcome: 'needs_clarification',
  spec: null,
  table: null,
  meta: null,
  chart: null,
  claims: [],
  answer_sentences: [],
  clarification: { question: 'Which metric or criterion should I use?', options: ['revenue', 'quantity'] },
  suggested_questions: [],
  trace: [],
};

export const insufficientAnomalyResult: AnalysisResult = {
  ...base,
  id: 'd1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1',
  question: 'Check row count by day for unusual values',
  anomalies: {
    status: 'insufficient_data',
    flags: [],
    reason: 'No series could be checked; 1 of 1 series skipped: 1 constant (no variation)',
    series_checked: 0,
    series_skipped: 1,
    excluded_values: 0,
  },
};

export const foundAnomalyResult: AnalysisResult = {
  ...base,
  id: 'e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2',
  spec: {
    ...base.spec!,
    dimensions: [],
    time_bucket: { column: { dataset: 'orders', column: 'order_date' }, grain: 'month' },
  },
  table: {
    columns: [
      { name: 'period', dtype: 'DATE', role: 'time' },
      { name: 'revenue', dtype: 'DOUBLE', role: 'metric' },
    ],
    rows: [
      ['2026-01-01', 18846.06],
      ['2026-02-01', 6282.02],
    ],
  },
  chart: { type: 'line', x: 'period', y: ['revenue'], series: null, title: 'Results over time' },
  anomalies: {
    status: 'completed_found',
    flags: [
      {
        method: 'robust_z',
        reference: 'own history of revenue (21 points over period)',
        row_key: { period: '2026-01-01' },
        metric: 'revenue',
        value: 18846.06,
        median: 6144.04,
        mad: 700.5,
        robust_z: 12.2,
        threshold: 3.5,
      },
    ],
    reason: null,
    series_checked: 1,
    series_skipped: 0,
    excluded_values: 0,
  },
};
