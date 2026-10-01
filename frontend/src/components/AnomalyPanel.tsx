import { useState } from 'react';
import type { AnalysisResult, AnomalyFlag, AnomalyReport } from '../api/types';
import { currencyFor, formatNumber } from '../lib/format';
import { entityLabel } from '../lib/resultView';

interface Props {
  report: AnomalyReport;
  result: AnalysisResult;
  onAskDrive?: (flag: AnomalyFlag) => void;
  onRetry?: () => void;
  /** open the method panel initially */
  methodOpen?: boolean;
}

const BANNER = {
  completed_found: { tag: 'Anomalies found', cls: 'found' },
  completed_none: { tag: 'None detected', cls: 'none' },
  insufficient_data: { tag: 'Insufficient data', cls: 'insufficient' },
  execution_failed: { tag: 'Detection failed', cls: 'failed' },
} as const;

function headline(report: AnomalyReport): string {
  const checked = `${report.series_checked} series checked`;
  switch (report.status) {
    case 'completed_found':
      return `${report.flags.length} unusual value${report.flags.length === 1 ? '' : 's'} flagged, ${checked}.`;
    case 'completed_none':
      return `Analysis completed: no values were outside the usual range, ${checked}.`;
    case 'insufficient_data':
      return report.reason ?? 'There is not enough data for this check.';
    default:
      return report.reason ?? 'The check could not be completed.';
  }
}

/** Renders a stored AnomalyReport: status, evidence per flag, method. Nothing is derived beyond formatting. */
export function AnomalyPanel({ report, result, onAskDrive, onRetry, methodOpen = false }: Props) {
  const [open, setOpen] = useState(methodOpen);
  const grain = result.spec?.time_bucket ?? undefined;
  const banner = BANNER[report.status];
  const references = [...new Set(report.flags.map((f) => f.reference))];
  const threshold = report.flags[0]?.threshold;

  return (
    <div className="anom">
      <div role="status" className={`anom__banner anom__banner--${banner.cls}`}>
        <span className="anom__tag">{banner.tag}</span>
        <span className="anom__headline">{headline(report)}</span>
        {report.status === 'execution_failed' && onRetry && (
          <button type="button" className="btn btn--danger btn--sm" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
      {report.reason && report.status !== 'insufficient_data' && report.status !== 'execution_failed' && (
        <p className="anom__note">{report.reason}</p>
      )}

      {report.flags.length > 0 && (
        <ul className="anom__flags">
          {report.flags.map((flag, i) => {
            const currency = currencyFor(result, flag.metric);
            const above = flag.value >= flag.median;
            return (
              <li key={i} className="anom__flag card">
                <div className="anom__flag-head">
                  <span className="serif anom__flag-title">{entityLabel(flag.row_key, grain)}</span>
                  <span className="mono anom__dir">{above ? 'ABOVE' : 'BELOW'} TYPICAL</span>
                </div>
                <dl className="anom__nums">
                  <div>
                    <dt className="eyebrow">Observed</dt>
                    <dd className="anom__big anom__big--flag">{formatNumber(flag.value, currency)}</dd>
                  </div>
                  <div>
                    <dt className="eyebrow">Typical (median)</dt>
                    <dd className="anom__big">{formatNumber(flag.median, currency)}</dd>
                  </div>
                </dl>
                <p className="mono anom__evidence">
                  MAD {formatNumber(flag.mad, currency)} · robust z {formatNumber(flag.robust_z)} · threshold {formatNumber(flag.threshold)}
                </p>
                <p className="anom__ref">Compared with: {flag.reference}</p>
                {onAskDrive && (
                  <button type="button" className="btn btn--sm" style={{ alignSelf: 'flex-start' }} onClick={() => onAskDrive(flag)}>
                    Ask what drove this →
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <div className="anom__method card">
        <button type="button" className="disclosure" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span>Method and threshold</span>
          <span className="mono">{open ? '−' : '+'}</span>
        </button>
        {open && (
          <dl className="kv anom__method-body">
            <dt>Method</dt>
            <dd>Robust z-score: |value − median| ÷ (1.4826 × MAD)</dd>
            <dt>Threshold</dt>
            <dd>{threshold !== undefined ? formatNumber(threshold) : '3.5'}</dd>
            <dt>Minimum data</dt>
            <dd>At least 8 non-missing points and a non-zero MAD in each series</dd>
            <dt>Series</dt>
            <dd>
              {report.series_checked} checked · {report.series_skipped} skipped
            </dd>
            <dt>Excluded values</dt>
            <dd>{report.excluded_values} missing value{report.excluded_values === 1 ? '' : 's'} left out</dd>
            <dt>Reference population</dt>
            <dd>{references.length ? references.join('; ') : 'Each series is compared with its own history'}</dd>
            <dt>Run</dt>
            <dd className="mono">{result.id}</dd>
          </dl>
        )}
      </div>
    </div>
  );
}
