import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { conversations as convApi, datasets as dsApi } from '../api/endpoints';
import type { AnalysisResult, AnalysisSpec, AnomalyFlag, DatasetDetail, DatasetMeta, Expr, Metric, TimeGrain } from '../api/types';
import { AnomalyPanel } from '../components/AnomalyPanel';
import { ChartFrame } from '../components/ChartFrame';
import { Spinner } from '../components/Spinner';
import { entityLabel } from '../lib/resultView';
import { useThreads } from '../state/threads';
import { useWorkspace } from '../state/workspace';

const CHECK_CONVERSATION = 'Anomaly checks';
const GRAINS: TimeGrain[] = ['day', 'week', 'month'];

interface Measure {
  key: string;
  label: string;
  metric: Metric;
}

const col = (dataset: string, column: string): Expr => ({ kind: 'col', ref: { dataset, column } });

function safeName(s: string): string {
  const n = s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return (/^[a-z]/.test(n) ? n : `m_${n}`).slice(0, 60);
}

/** Measures offered for a dataset. "Revenue" is the documented metric definition
 * (quantity × unit_price − discount_amount) and is offered only when those columns exist. */
function measuresFor(detail: DatasetDetail, dataset: string): Measure[] {
  const names = new Set(detail.profile.columns.map((c) => c.name));
  const out: Measure[] = [];
  if (['quantity', 'unit_price', 'discount_amount'].every((n) => names.has(n))) {
    out.push({
      key: 'revenue',
      label: 'Revenue',
      metric: {
        name: 'revenue',
        agg: 'sum',
        expr: {
          kind: 'binop',
          op: '-',
          left: { kind: 'binop', op: '*', left: col(dataset, 'quantity'), right: col(dataset, 'unit_price') },
          right: col(dataset, 'discount_amount'),
        },
      },
    });
  }
  out.push({ key: 'rows', label: 'Row count', metric: { name: 'row_count', agg: 'count', expr: null } });
  for (const c of detail.profile.columns) {
    if (c.role_hint === 'metric' && c.name !== 'revenue') {
      out.push({ key: `sum:${c.name}`, label: `Total ${c.name}`, metric: { name: `total_${safeName(c.name)}`, agg: 'sum', expr: col(dataset, c.name) } });
    }
  }
  return out;
}

type Run = { status: 'idle' } | { status: 'running' } | { status: 'done'; result: AnalysisResult } | { status: 'error'; message: string };

export function AnomaliesScreen() {
  const ws = useWorkspace();
  const threads = useThreads();
  const navigate = useNavigate();
  const datasets = ws.datasets;
  const [details, setDetails] = useState<Record<string, DatasetDetail>>({});
  const [datasetId, setDatasetId] = useState<string>('');
  const [measureKey, setMeasureKey] = useState('');
  const [grain, setGrain] = useState<TimeGrain>('day');
  const [groupBy, setGroupBy] = useState('');
  const [timeCol, setTimeCol] = useState('');
  const [run, setRun] = useState<Run>({ status: 'idle' });

  // Profiles tell us which datasets have a date column and which measures make sense.
  useEffect(() => {
    for (const d of datasets ?? []) {
      const key = `${d.id}@${d.version}`;
      if (details[key]) continue;
      dsApi
        .detail(d.id)
        .then((det) => setDetails((m) => ({ ...m, [key]: det })))
        .catch(() => undefined);
    }
  }, [datasets, details]);

  const dated = (datasets ?? []).filter((d) => details[`${d.id}@${d.version}`]?.profile.columns.some((c) => c.role_hint === 'time'));
  // Prefer a dataset that offers the documented revenue measure, then any dated dataset.
  const hasRevenue = (d: DatasetMeta) => details[`${d.id}@${d.version}`]?.profile.columns.some((c) => c.name === 'discount_amount');
  const dataset: DatasetMeta | undefined = dated.find((d) => d.id === datasetId) ?? dated.find(hasRevenue) ?? dated[0];
  const detail = dataset ? details[`${dataset.id}@${dataset.version}`] : undefined;
  const measures = useMemo(() => (detail && dataset ? measuresFor(detail, dataset.name) : []), [detail, dataset]);
  const measure = measures.find((m) => m.key === measureKey) ?? measures[0];
  const timeCols = detail?.profile.columns.filter((c) => c.role_hint === 'time') ?? [];
  const groupCols = detail?.profile.columns.filter((c) => c.role_hint === 'dimension' && c.distinct_count > 1 && c.distinct_count <= 50) ?? [];
  const time = timeCols.find((c) => c.name === timeCol) ?? timeCols[0];

  const loadingProfiles = datasets !== null && (datasets ?? []).length > 0 && (datasets ?? []).some((d) => !details[`${d.id}@${d.version}`]);

  const execute = async () => {
    if (!dataset || !measure || !time || run.status === 'running') return;
    setRun({ status: 'running' });
    const spec: AnalysisSpec = {
      base_dataset: dataset.name,
      metrics: [measure.metric],
      dimensions: groupBy ? [{ dataset: dataset.name, column: groupBy }] : [],
      time_bucket: { column: { dataset: dataset.name, column: time.name }, grain },
      filters: [],
      joins: [],
      sort: [{ by: 'time_bucket', descending: false }],
      limit: null,
    };
    try {
      let conv = ws.conversations.find((c) => c.title === CHECK_CONVERSATION);
      if (!conv) {
        conv = await convApi.create(CHECK_CONVERSATION, [dataset.id]);
        const created = conv;
        ws.setConversations((list) => [created, ...list]);
      }
      const result = await convApi.analyze(conv.id, {
        question: `Check ${measure.label.toLowerCase()} by ${grain}${groupBy ? ` and ${groupBy}` : ''} for unusual values`,
        spec,
        anomaly_metric: measure.metric.name,
        dataset_ids: [dataset.id],
        request_key: crypto.randomUUID(),
      });
      setRun({ status: 'done', result });
    } catch (e) {
      setRun({ status: 'error', message: e instanceof ApiError ? e.message : 'The check could not be completed.' });
    }
  };

  const askDrive = (flag: AnomalyFlag, result: AnalysisResult) => {
    navigate('/ask');
    void threads.ask(`What drove ${flag.metric.replace(/_/g, ' ')} for ${entityLabel(flag.row_key, result.table, result.spec?.time_bucket?.grain)}?`, null);
  };

  const result = run.status === 'done' ? run.result : null;
  const report = result?.anomalies ?? null;

  return (
    <div className="page">
      <div className="page__inner" style={{ maxWidth: 1100 }}>
        <div>
          <h2 className="page-title">Unusual observations</h2>
          <p className="page-lede">Flags mark values far outside their usual range. They are worth investigating — not proof of an error or fraud.</p>
        </div>

        {datasets && !loadingProfiles && !dataset && (
          <div className="empty-box" style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start' }}>
            <div className="serif" style={{ fontSize: 24, color: 'var(--ink)' }}>
              Anomaly detection needs a dated measure
            </div>
            <div style={{ fontSize: 13.5, lineHeight: 1.5, maxWidth: 620 }}>
              {datasets.length
                ? 'None of your datasets has a date column. Upload a file with dates, or load the sample data.'
                : 'Upload a file with a date column and a numeric measure, or load the sample data to try the explorer.'}
            </div>
            <button type="button" className="btn btn--primary" onClick={() => void ws.loadSample()}>
              Load sample data
            </button>
          </div>
        )}

        {dataset && detail && measure && time && (
          <form
            className="card anom-form"
            onSubmit={(e) => {
              e.preventDefault();
              void execute();
            }}
          >
            <label className="anom-form__f">
              <span className="eyebrow">Dataset</span>
              <select
                className="select mono"
                value={dataset.id}
                onChange={(e) => {
                  setDatasetId(e.target.value);
                  setMeasureKey('');
                  setGroupBy('');
                  setTimeCol('');
                  setRun({ status: 'idle' });
                }}
              >
                {dated.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.filename}
                  </option>
                ))}
              </select>
            </label>
            <div className="anom-form__f">
              <span className="eyebrow" id="anom-measure">
                Measure
              </span>
              <div role="radiogroup" aria-labelledby="anom-measure" className="segmented segmented--wrap">
                {measures.map((m) => (
                  <button
                    key={m.key}
                    type="button"
                    role="radio"
                    aria-checked={m.key === measure.key}
                    onClick={() => {
                      setMeasureKey(m.key);
                      setRun({ status: 'idle' });
                    }}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="anom-form__f">
              <span className="eyebrow" id="anom-grain">
                Time step
              </span>
              <div role="radiogroup" aria-labelledby="anom-grain" className="segmented">
                {GRAINS.map((g) => (
                  <button
                    key={g}
                    type="button"
                    role="radio"
                    aria-checked={g === grain}
                    onClick={() => {
                      setGrain(g);
                      setRun({ status: 'idle' });
                    }}
                  >
                    {g}
                  </button>
                ))}
              </div>
            </div>
            {timeCols.length > 1 && (
              <label className="anom-form__f">
                <span className="eyebrow">Date column</span>
                <select className="select mono" value={time.name} onChange={(e) => setTimeCol(e.target.value)}>
                  {timeCols.map((c) => (
                    <option key={c.name}>{c.name}</option>
                  ))}
                </select>
              </label>
            )}
            <label className="anom-form__f">
              <span className="eyebrow">Compare each…</span>
              <select
                className="select mono"
                value={groupBy}
                onChange={(e) => {
                  setGroupBy(e.target.value);
                  setRun({ status: 'idle' });
                }}
              >
                <option value="">whole dataset (one series)</option>
                {groupCols.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name} (each against its own history)
                  </option>
                ))}
              </select>
            </label>
            <span style={{ flex: 1 }} />
            <button type="submit" className="btn btn--primary" disabled={run.status === 'running'}>
              {run.status === 'running' ? 'Running…' : run.status === 'idle' ? 'Run detection' : 'Run again'}
            </button>
          </form>
        )}

        {loadingProfiles && !dataset && <div className="notice-box">Reading your datasets…</div>}

        {run.status === 'idle' && dataset && (
          <div className="empty-box">Choose a measure and run detection. Nothing is flagged until a run completes.</div>
        )}

        {run.status === 'running' && (
          <div className="running" aria-live="polite" role="status">
            <div className="running__step">
              <Spinner />
              <span>Running the structured analysis and the robust z-score check…</span>
            </div>
          </div>
        )}

        {run.status === 'error' && (
          <AnomalyFailure message={run.message} onRetry={() => void execute()} />
        )}

        {result && !report && <AnomalyFailure message={result.message ?? 'The analysis returned no anomaly report.'} onRetry={() => void execute()} />}

        {result && report && (
          <>
            <AnomalyPanel report={report} result={result} onAskDrive={(f) => askDrive(f, result)} onRetry={() => void execute()} methodOpen />
            {result.table && result.chart && (
              <div className="card" style={{ padding: '12px 14px' }}>
                <ChartFrame table={result.table} chart={result.chart} result={result} grain={result.spec?.time_bucket?.grain} flags={report.flags} height={340} label={result.chart.title} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function AnomalyFailure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="status" className="anom__banner anom__banner--failed">
      <span className="anom__tag">Detection failed</span>
      <span className="anom__headline">{message}</span>
      <button type="button" className="btn btn--danger btn--sm" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}
