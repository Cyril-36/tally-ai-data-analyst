import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { datasets as dsApi, pins as pinsApi } from '../api/endpoints';
import type { AnalysisResult, Overview } from '../api/types';
import { currencyFor, formatCell } from '../lib/format';
import { useToast } from '../state/toast';
import { useWorkspace } from '../state/workspace';
import { ChartFrame } from './ChartFrame';

const SKIPPED_LABEL: Record<string, string> = {
  trend: 'Trend over time',
  breakdown: 'Top groups',
  mix: 'Rows by category',
};

/**
 * Automatic overview of one dataset. The server chooses the cards from the dataset profile
 * and computes each as a stored, validated result; this component only renders them.
 */
export function OverviewSection() {
  const ws = useWorkspace();
  const { toast } = useToast();
  const datasets = ws.datasets ?? [];
  const [datasetId, setDatasetId] = useState('');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [error, setError] = useState('');
  const [pinned, setPinned] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!datasetId && datasets.length) setDatasetId(ws.selectedIds.find((id) => datasets.some((d) => d.id === id)) ?? datasets[0].id);
  }, [datasetId, datasets, ws.selectedIds]);

  const generate = async () => {
    if (!datasetId) return;
    setState('loading');
    setError('');
    try {
      setOverview(await dsApi.overview(datasetId));
      setState('idle');
    } catch (e) {
      setOverview(null);
      setError(e instanceof ApiError ? e.message : 'The overview could not be generated.');
      setState('error');
    }
  };

  const pin = async (result: AnalysisResult) => {
    try {
      await pinsApi.create(result.id, `overview-${result.id}`);
      setPinned((s) => new Set(s).add(result.id));
      await ws.refreshPins();
    } catch (e) {
      toast(`Could not pin: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
    }
  };

  if (!datasets.length) return null;
  const totals = overview?.cards.find((c) => c.kind === 'totals');
  const charts = overview?.cards.filter((c) => c.kind !== 'totals') ?? [];
  const name = datasets.find((d) => d.id === datasetId)?.name ?? '';

  return (
    <section className="overview" aria-labelledby="overview-title">
      <div className="overview__bar">
        <div>
          <h3 id="overview-title" className="overview__title">Overview</h3>
          <p className="page-lede" style={{ margin: 0 }}>
            Totals, a trend, top groups and a category mix, chosen from the dataset’s columns. No AI is used.
          </p>
        </div>
        <div className="overview__controls">
          <select className="select" aria-label="Dataset for the overview" value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn--primary" disabled={state === 'loading' || !datasetId} onClick={() => void generate()}>
            {state === 'loading' ? 'Generating…' : overview?.dataset_id === datasetId ? 'Refresh overview' : 'Generate overview'}
          </button>
        </div>
      </div>

      {state === 'error' && (
        <p role="alert" className="inline-alert">
          {error}
        </p>
      )}

      {overview && overview.dataset_id === datasetId && (
        <>
          {totals?.result.table && (
            <dl className="overview__kpis" aria-label={`Headline totals for ${name}`}>
              {totals.result.table.columns.map((col, i) => (
                <div key={col.name} className="card overview__kpi">
                  <dt className="overview__kpi-label">{col.name === 'rows' ? 'Rows' : col.name.replace(/^total_/, 'Total ').replace(/_/g, ' ')}</dt>
                  <dd className="overview__kpi-value serif">
                    {formatCell(totals.result.table!.rows[0]?.[i] ?? null, col, { currency: currencyFor(totals.result, col.name) })}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          <ul className="pins">
            {charts.map(({ kind, result, status }) => (
              <li key={result.id} className={`card pin${status !== 'current' ? ' pin--stale' : ''}`} aria-label={result.question}>
                <div className="pin__head">
                  <span className="pin__title">{result.question}</span>
                  <button
                    type="button"
                    className="btn btn--sm"
                    disabled={pinned.has(result.id)}
                    onClick={() => void pin(result)}
                    aria-label={pinned.has(result.id) ? `“${result.question}” is pinned` : `Pin “${result.question}” to the dashboard`}
                  >
                    {pinned.has(result.id) ? 'Pinned ✓' : 'Pin'}
                  </button>
                </div>
                <div className="pin__body">
                  {result.outcome === 'ok' && result.answer_sentences[0] && <p className="serif pin__conclusion">{result.answer_sentences[0]}</p>}
                  {result.table && result.chart && (
                    <ChartFrame table={result.table} chart={result.chart} result={result} grain={result.spec?.time_bucket?.grain ?? null} mini label={result.question} />
                  )}
                  {kind === 'trend' && result.anomalies?.status === 'completed_found' && result.answer_sentences[1] && (
                    <p className="overview__note">{result.answer_sentences[1]}</p>
                  )}
                  {result.meta?.caveats
                    .filter((c) => c.code === 'partial_period')
                    .map((c) => (
                      <p key={c.code} className="overview__note">
                        Note: {c.message}
                      </p>
                    ))}
                </div>
              </li>
            ))}
          </ul>
          {overview.skipped.length > 0 && (
            <ul className="overview__skipped" aria-label="Cards not shown">
              {overview.skipped.map((s) => (
                <li key={s.kind}>
                  <strong>{SKIPPED_LABEL[s.kind] ?? s.kind}:</strong> {s.reason}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
