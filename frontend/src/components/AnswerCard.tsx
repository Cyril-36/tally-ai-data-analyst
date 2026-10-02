import { useMemo, useState } from 'react';
import type { AnalysisResult, AnomalyFlag, ChartSpec, DatasetMeta, ResultStatus } from '../api/types';
import { candidateChartTypes } from '../lib/figure';
import { clock, currencyFor, formatWithUnit, shortId } from '../lib/format';
import { contextItems, countChecks, currencyChanges, detailItems, formatParam, sourceVersions, staleLabel, traceLine } from '../lib/resultView';
import { AnomalyPanel } from './AnomalyPanel';
import { ChartFrame } from './ChartFrame';
import { ResultTable } from './ResultTable';

export interface AnswerCardProps {
  result: AnalysisResult;
  status: ResultStatus;
  datasets: DatasetMeta[];
  domId?: string;
  highlighted?: boolean;
  /** seconds the question took, when known (live answers only) */
  seconds?: number;
  pinned: boolean;
  actionsDisabled: boolean;
  detailsOpen: boolean;
  allRows: boolean;
  copied: boolean;
  onToggleDetails: () => void;
  onToggleRows: () => void;
  onCopySql: () => void;
  onPin: () => void;
  onExport: (format: 'csv' | 'html') => void;
  onChartType: (chart: ChartSpec) => void;
  onAsk: (question: string) => void;
  onAskDrive?: (flag: AnomalyFlag) => void;
}

/**
 * One answer, rendered strictly from the stored AnalysisResult: the backend's own sentences,
 * its table, a chart built from table + chart spec, its SQL, caveats and trace. Only an
 * `ok` outcome is ever called validated.
 */
export function AnswerCard(props: AnswerCardProps) {
  const { result, status, datasets } = props;
  const verified = result.outcome === 'ok';
  const withheld = result.outcome === 'narrative_unverified';
  const table = result.table;
  const chart = result.chart;
  const grain = result.spec?.time_bucket?.grain ?? null;
  const sources = useMemo(() => sourceVersions(result, datasets), [result, datasets]);
  const ctx = useMemo(() => contextItems(result.spec), [result.spec]);
  const details = useMemo(() => detailItems(result, datasets), [result, datasets]);
  const types = useMemo(() => (table && chart ? candidateChartTypes(table, chart) : []), [table, chart]);
  const changes = useMemo(() => currencyChanges(result, datasets), [result, datasets]);
  const stale = staleLabel(status, sources, changes);
  const checks = countChecks(result);
  const [moreStatements, setMoreStatements] = useState(false);
  const sentences = result.answer_sentences;
  const shownSentences = moreStatements ? sentences : sentences.slice(0, 3);
  // Headline figures come from rank / share / comparison claims; plain values are already in the table.
  const kpis = verified
    ? result.claims
        .filter((c) => c.kind === 'rank' || c.kind === 'share' || c.kind === 'comparison')
        .slice(0, 3)
        .map((c) => ({
          key: c.id,
          value: formatWithUnit(c.value, c.unit ?? currencyFor(result, c.metric)),
          label: [c.metric.replace(/_/g, ' '), ...Object.values(c.entity).map(String), c.period].filter(Boolean).join(' · '),
        }))
    : [];
  const caveats = result.meta?.caveats ?? [];
  const meta = result.meta;

  return (
    <article id={props.domId} aria-label={result.question} className={`answer card${props.highlighted ? ' answer--hl' : ''}`}>
      <div className="answer__bar mono">
        {verified ? <span className="answer__ok">✓ VALIDATED</span> : <span className="answer__warn">SUMMARY WITHHELD</span>}
        <span>{shortId(result.id)}</span>
        <span>{clock(result.created_at)}</span>
        {(checks > 0 || props.seconds !== undefined) && (
          <span>
            {[checks > 0 ? `${checks} check${checks === 1 ? '' : 's'}` : '', props.seconds !== undefined ? `${props.seconds.toFixed(1)}s` : ''].filter(Boolean).join(' · ')}
          </span>
        )}
        {stale && <span className={`answer__stale${status === 'missing' ? ' answer__stale--missing' : ''}`}>{stale}</span>}
      </div>

      <div className="answer__lead">
        <h3 className="answer__title">{result.question}</h3>
        {verified &&
          shownSentences.map((s, i) => (
            <p key={i} className={`serif ${i === 0 ? 'answer__conclusion' : 'answer__sentence'}`}>
              {s}
            </p>
          ))}
        {verified && sentences.length > 3 && (
          <button type="button" className="btn btn--link btn--xs" style={{ alignSelf: 'flex-start' }} onClick={() => setMoreStatements((v) => !v)}>
            {moreStatements ? 'Show fewer statements' : `Show ${sentences.length - 3} more statements`}
          </button>
        )}
        {withheld && (
          <div role="note" className="inline-warn">
            <strong>Summary withheld.</strong> {result.message ?? 'The written summary could not be validated, so it is not shown.'} The table
            and chart below come straight from the calculation.
          </div>
        )}
        {kpis.length > 0 && (
          <dl className="answer__kpis">
            {kpis.map((k) => (
              <div key={k.key}>
                <dt className="answer__kpi-l">{k.label}</dt>
                <dd className="mono answer__kpi-v">{k.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {ctx.length > 0 && (
        <dl className="answer__ctx">
          {ctx.map((c) => (
            <div key={c.k}>
              <dt className="eyebrow answer__ctx-k">{c.k}</dt>
              <dd className="mono answer__ctx-v">{c.v}</dd>
            </div>
          ))}
        </dl>
      )}

      {table && (
        <section className="answer__sec" aria-label="Chart">
          <div className="answer__sec-head">
            <span className="eyebrow">Chart</span>
            {chart && types.length > 1 && (
              <div role="group" aria-label="Chart type" className="segmented">
                {types.map((t) => (
                  <button key={t} type="button" aria-pressed={t === chart.type} onClick={() => t !== chart.type && props.onChartType({ ...chart, type: t })}>
                    {t}
                  </button>
                ))}
              </div>
            )}
          </div>
          {chart ? (
            <ChartFrame table={table} chart={chart} result={result} grain={grain} flags={result.anomalies?.flags} />
          ) : (
            <div role="note" className="notice-box">
              <strong>No chart for this result.</strong> The data has no shape the chart types can draw. The table below is complete.
            </div>
          )}
        </section>
      )}

      {table && (
        <section className="answer__sec" aria-label="Result table">
          <ResultTable result={result} label={result.question} expanded={props.allRows} onToggle={props.onToggleRows} />
        </section>
      )}

      {result.anomalies && (
        <section className="answer__sec" aria-label="Anomaly check">
          <span className="eyebrow">Unusual values</span>
          <div style={{ marginTop: 6 }}>
            <AnomalyPanel report={result.anomalies} result={result} onAskDrive={props.onAskDrive} />
          </div>
        </section>
      )}

      <section className="answer__sec" aria-label="Assumptions and limitations">
        <span className="eyebrow">Assumptions &amp; limitations</span>
        <ul className="answer__list">
          {caveats.length ? (
            caveats.map((c, i) => <li key={`${c.code}-${i}`}>{c.message}</li>)
          ) : (
            <li>No data caveats were recorded for this calculation.</li>
          )}
        </ul>
      </section>

      <div className="answer__details">
        <button type="button" className="disclosure" aria-expanded={props.detailsOpen} onClick={props.onToggleDetails}>
          <span>Analysis details — sources, method, SQL</span>
          <span className="mono">{props.detailsOpen ? '−' : '+'}</span>
        </button>
        {props.detailsOpen && (
          <div className="answer__details-body">
            <dl className="kv">
              {details.map((d) => (
                <div key={d.k} style={{ display: 'contents' }}>
                  <dt>{d.k}</dt>
                  <dd>{d.v}</dd>
                </div>
              ))}
            </dl>
            {meta ? (
              <>
                <div className="sql">
                  <pre aria-label="SQL that ran">{meta.sql}</pre>
                  <button type="button" onClick={props.onCopySql}>
                    {props.copied ? 'Copied ✓' : 'Copy SQL'}
                  </button>
                </div>
                <div className="params mono">
                  <span className="eyebrow">Parameters</span>
                  {meta.params.length ? (
                    meta.params.map((p, i) => (
                      <span key={i}>
                        ?{i + 1} = {formatParam(p)}
                      </span>
                    ))
                  ) : (
                    <span>none</span>
                  )}
                </div>
              </>
            ) : (
              <p className="answer__muted">No SQL ran for this result.</p>
            )}
            {result.trace.length > 0 && (
              <div>
                <span className="eyebrow">Trace</span>
                <ol className="trace mono">
                  {result.trace.map((t, i) => {
                    const line = traceLine(t);
                    return (
                      <li key={i}>
                        <strong>{line.label}</strong> {line.detail}
                      </li>
                    );
                  })}
                </ol>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="answer__actions">
        <button type="button" className={`btn ${props.pinned ? 'btn--soft' : 'btn--primary'}`} onClick={props.onPin}>
          {props.pinned ? 'Pinned ✓ — view' : 'Pin to dashboard'}
        </button>
        <button type="button" className="btn" onClick={() => props.onExport('csv')}>
          Download CSV
        </button>
        <button type="button" className="btn" onClick={() => props.onExport('html')}>
          Export HTML report
        </button>
      </div>

      {result.suggested_questions.length > 0 && (
        <div className="answer__follow">
          <span className="answer__follow-l">Follow up:</span>
          {result.suggested_questions.map((q) => (
            <button key={q} type="button" className="pill" disabled={props.actionsDisabled} onClick={() => props.onAsk(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}
