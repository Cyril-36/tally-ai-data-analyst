/** TypeScript mirror of backend/app/contracts (result.py, outcomes.py, events.py, spec.py). */

export type Outcome =
  | 'ok'
  | 'permission_denied'
  | 'not_found'
  | 'needs_clarification'
  | 'unsupported'
  | 'insufficient_data'
  | 'execution_failed'
  | 'provider_failed'
  | 'budget_exhausted'
  | 'ai_disabled'
  | 'narrative_unverified'
  | 'invalid_input';

export interface ErrorBody {
  outcome: Outcome;
  message: string;
}

export type ColumnRole = 'dimension' | 'time' | 'metric';

export interface ResultColumn {
  name: string;
  dtype: string;
  role: ColumnRole;
}

export type Cell = string | number | boolean | null;

export interface ResultTable {
  columns: ResultColumn[];
  rows: Cell[][];
}

export interface Caveat {
  code: string;
  message: string;
  count?: number | null;
}

export interface ExecutionMeta {
  sql: string;
  params: unknown[];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  dataset_versions: Record<string, number>;
  caveats: Caveat[];
  /** Declared currency of each source dataset (by id) when this result was calculated. */
  source_currencies?: Record<string, string | null>;
}

export type ChartType = 'bar' | 'line' | 'area' | 'pie' | 'scatter' | 'histogram';

export interface ChartSpec {
  type: ChartType;
  x: string;
  y: string[];
  series?: string | null;
  title: string;
}

export interface Claim {
  id: string;
  kind: 'value' | 'rank' | 'comparison' | 'share' | 'trend';
  metric: string;
  entity: Record<string, unknown>;
  period?: string | null;
  value: number;
  unit?: string | null;
  source: unknown;
  rank?: number | null;
  rank_of?: number | null;
  direction?: string | null;
  compared_to?: string | null;
  periods_covered?: number | null;
}

export type AnomalyStatus =
  | 'completed_none'
  | 'completed_found'
  | 'insufficient_data'
  | 'execution_failed';

export interface AnomalyFlag {
  method: 'robust_z';
  reference: string;
  row_key: Record<string, Cell>;
  metric: string;
  value: number;
  median: number;
  mad: number;
  robust_z: number;
  threshold: number;
}

export interface AnomalyReport {
  status: AnomalyStatus;
  flags: AnomalyFlag[];
  reason?: string | null;
  series_checked: number;
  series_skipped: number;
  excluded_values: number;
}

export interface Clarification {
  question: string;
  options: string[];
}

/* ---- AnalysisSpec (only the parts the UI reads or builds) ---- */
export interface ColumnRef {
  dataset: string;
  column: string;
}
export type Expr =
  | { kind: 'col'; ref: ColumnRef }
  | { kind: 'num'; value: number }
  | { kind: 'binop'; op: '+' | '-' | '*' | '/'; left: Expr; right: Expr };

export type Aggregate = 'sum' | 'avg' | 'count' | 'count_distinct' | 'min' | 'max' | 'median';

export interface Metric {
  name: string;
  agg: Aggregate;
  expr?: Expr | null;
}
export interface SpecFilter {
  column: ColumnRef;
  op: string;
  value?: string | number | boolean | Array<string | number | boolean> | null;
}
export type TimeGrain = 'day' | 'week' | 'month' | 'quarter' | 'year';
export interface AnalysisSpec {
  base_dataset: string;
  metrics: Metric[];
  dimensions: ColumnRef[];
  time_bucket?: { column: ColumnRef; grain: TimeGrain } | null;
  filters: SpecFilter[];
  joins: Array<{ left: ColumnRef; right: ColumnRef; pre_aggregate?: unknown }>;
  sort: Array<{ by: string; descending: boolean }>;
  limit?: number | null;
}

export interface AnalysisResult {
  id: string;
  owner_id: string;
  conversation_id: string;
  question: string;
  outcome: Outcome;
  message?: string | null;
  spec?: AnalysisSpec | null;
  table?: ResultTable | null;
  meta?: ExecutionMeta | null;
  chart?: ChartSpec | null;
  claims: Claim[];
  answer_sentences: string[];
  anomalies?: AnomalyReport | null;
  clarification?: Clarification | null;
  suggested_questions: string[];
  trace: Array<Record<string, unknown>>;
  created_at: string;
}

export type ResultStatus = 'current' | 'stale' | 'missing';

export interface StoredResult {
  result: AnalysisResult;
  status: ResultStatus;
}

/* ---- SSE ---- */
export type ProgressEventType = 'planning' | 'tool_call' | 'tool_result' | 'executing' | 'validating';
export type StreamEvent =
  | { type: ProgressEventType; data: Record<string, unknown> }
  | { type: 'result'; data: AnalysisResult }
  | { type: 'error'; data: ErrorBody };

/* ---- auth ---- */
export interface User {
  id: string;
  email: string;
  role: 'analyst' | 'admin' | string;
}

/* ---- datasets ---- */
export interface DatasetMeta {
  id: string;
  name: string;
  filename: string;
  version: number;
  row_count: number;
  created_at: string;
  updated_at: string;
  /** Declared by the user (or the sample data); money is only shown with a symbol when set. */
  currency: 'GBP' | 'USD' | 'EUR' | null;
}

export interface SampleLoadResult {
  created: DatasetMeta[];
  existing: DatasetMeta[];
}

export interface ProfileColumn {
  name: string;
  dtype: string;
  role_hint: 'id' | 'time' | 'dimension' | 'metric' | string;
  null_count: number;
  distinct_count: number;
  sample_values: Array<string | number | null>;
  min: string | number | null;
  max: string | number | null;
}

export interface DatasetProfile {
  columns: ProfileColumn[];
  row_count: number;
  warnings: string[];
}

export interface RowsPage {
  columns: string[];
  rows: Array<Array<string | number | boolean | null>>;
  offset?: number;
  limit?: number;
  total?: number;
}

export interface DatasetDetail extends DatasetMeta {
  profile: DatasetProfile;
  preview: { columns: string[]; rows: RowsPage['rows'] };
}

export interface QualityIssue {
  level: 'warning' | 'info' | string;
  code: string;
  column: string | null;
  message: string;
  count: number | null;
}

export interface QualityReport {
  score: number;
  penalties: Record<string, number>;
  row_count: number;
  reference_date: string;
  columns: Array<{ name: string; null_count: number; null_pct: number }>;
  duplicate_rows: number;
  candidate_keys: string[];
  issues: QualityIssue[];
}

export interface Dependents {
  dataset_id: string;
  results: number;
  pins: number;
}

/* ---- workspace ---- */
export interface ConversationState {
  dataset_ids?: string[];
  [key: string]: unknown;
}
export interface Conversation {
  id: string;
  title: string;
  state: ConversationState;
  created_at: string;
}

export interface Pin {
  id: string;
  result_id: string;
  title: string;
  position: number;
  created_at?: string;
}
export interface DashboardItem {
  id: string;
  result_id: string;
  title: string;
  position: number;
  status: ResultStatus;
  result: AnalysisResult;
}

/* ---- admin ---- */
export interface SystemSettings {
  max_upload_mb: number;
  llm_enabled: boolean;
  sample_data_enabled?: boolean;
  /** days; null means never */
  retention_days?: number | null;
}
export interface ProviderTest {
  ok: boolean;
  latency_ms: number;
  model: string;
}
