import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { datasets as dsApi } from '../api/endpoints';
import type { DatasetMeta, QualityIssue } from '../api/types';
import { useWorkspace } from '../state/workspace';

interface CodeInfo {
  kind: string;
  check: string;
  unit: string;
  impact: string;
}

// How each check is treated is fixed by the backend (data is never cleaned or edited).
const CODES: Record<string, CodeInfo> = {
  nulls: {
    kind: 'Missing values',
    check: 'nulls',
    unit: 'empty',
    impact: 'Empty cells are left as they are. Calculations over this column skip them and the answer lists the count as a caveat.',
  },
  duplicate_rows: {
    kind: 'Duplicate rows',
    check: 'duplicates',
    unit: 'rows',
    impact: 'Duplicates are not removed. Totals, counts and averages include every copy.',
  },
  numeric_stored_as_text: {
    kind: 'Numbers stored as text',
    check: 'numeric_text',
    unit: 'values',
    impact: 'The column is read as text, so it cannot be summed or averaged until the source file is corrected.',
  },
  category_variants: {
    kind: 'Inconsistent categories',
    check: 'variants',
    unit: 'groups',
    impact: 'Each spelling is a separate value, so grouped totals are split across the variants.',
  },
  future_dates: {
    kind: 'Future dates',
    check: 'future_dates',
    unit: 'dates',
    impact: 'Rows with dates after the reference date are included in totals and can distort recent periods.',
  },
  negative_values: {
    kind: 'Negative values',
    check: 'negatives',
    unit: 'values',
    impact: 'Informational. Negative values are included in sums and averages exactly as uploaded.',
  },
};

export function QualityScreen() {
  const ws = useWorkspace();
  const list = ws.datasets ?? [];
  return (
    <div className="page">
      <div className="page__inner" style={{ maxWidth: 1000 }}>
        <div>
          <h2 className="page-title">Data quality</h2>
          <p className="page-lede">
            Tally never cleans or edits your files. These findings describe what looks off and how each analysis treats it, so you can judge the
            answers.
          </p>
        </div>
        {ws.datasets && list.length === 0 && (
          <div className="empty-box">Upload a file or load the sample data to see quality findings.</div>
        )}
        {list.map((d) => (
          <DatasetQuality key={`${d.id}@${d.version}`} dataset={d} />
        ))}
      </div>
    </div>
  );
}

function DatasetQuality({ dataset }: { dataset: DatasetMeta }) {
  const ws = useWorkspace();
  const entry = ws.quality[ws.qualityKey(dataset)];
  const issues = entry?.state === 'ready' ? entry.report.issues : [];
  const report = entry?.state === 'ready' ? entry.report : null;

  return (
    <section className="card q-group" aria-label={`Quality of ${dataset.name}`}>
      <div className="q-group__head">
        <div className="q-group__title">
          <h3 className="serif" style={{ fontSize: 24 }}>
            {dataset.name}
          </h3>
          <span className="mono q-group__meta">
            <Link to={`/data/${dataset.id}`}>{dataset.filename}</Link> · {dataset.row_count.toLocaleString('en-GB')} rows
          </span>
        </div>
        <span className="mono q-group__count" style={{ color: issues.length ? 'var(--warn)' : 'var(--accent)' }}>
          {!entry || entry.state === 'loading' ? 'Checking…' : entry.state === 'error' ? 'Unavailable' : issues.length ? `${issues.length} finding${issues.length > 1 ? 's' : ''}` : 'No issues'}
        </span>
      </div>
      {entry?.state === 'error' && (
        <p role="alert" className="inline-alert" style={{ margin: 16 }}>
          {entry.message}
        </p>
      )}
      {report && (
        <div className="q-score mono">
          Quality score {report.score}/100
          {report.candidate_keys.length > 0 && <> · unique key: {report.candidate_keys.join(', ')}</>}
        </div>
      )}
      {report && issues.length === 0 && (
        <div className="q-none">✓ No missing values, duplicates or type problems found in {dataset.filename}.</div>
      )}
      {issues.map((issue, i) => (
        <Finding key={`${issue.code}-${issue.column}-${i}`} dataset={dataset} issue={issue} />
      ))}
    </section>
  );
}

function countLabel(count: number, unit: string): string {
  const one: Record<string, string> = { rows: 'row', values: 'value', groups: 'group', dates: 'date', empty: 'empty' };
  return `${count.toLocaleString('en-GB')} ${count === 1 ? (one[unit] ?? unit) : unit}`.trim();
}

type Examples = { columns: string[]; rows: Array<Array<string | number | boolean | null>> };

function parseExamples(data: unknown): Examples | null {
  if (!data || typeof data !== 'object') return null;
  const obj = data as Record<string, unknown>;
  if (Array.isArray(obj.columns) && Array.isArray(obj.rows)) return { columns: obj.columns.map(String), rows: obj.rows as Examples['rows'] };
  if (obj.examples) return parseExamples(obj.examples);
  const arr = Array.isArray(data) ? data : Array.isArray(obj.rows) ? obj.rows : null;
  if (arr && arr.length && typeof arr[0] === 'object' && !Array.isArray(arr[0])) {
    const columns = Object.keys(arr[0] as object);
    return { columns, rows: (arr as Array<Record<string, never>>).map((r) => columns.map((c) => r[c] ?? null)) };
  }
  return null;
}

function Finding({ dataset, issue }: { dataset: DatasetMeta; issue: QualityIssue }) {
  const info = CODES[issue.code] ?? { kind: issue.code.replace(/_/g, ' '), check: issue.code, unit: '', impact: 'See the Data screen for the affected column.' };
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<{ status: 'idle' | 'loading' | 'ready' | 'error'; examples?: Examples | null; message?: string }>({ status: 'idle' });

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && state.status === 'idle') {
      setState({ status: 'loading' });
      dsApi
        .qualityExamples(dataset.id, info.check, issue.column)
        .then((data) => setState({ status: 'ready', examples: parseExamples(data) }))
        .catch((e) => setState({ status: 'error', message: e instanceof ApiError ? e.message : 'Examples could not be loaded.' }));
    }
  };

  const panelId = `q-ex-${dataset.id}-${issue.code}-${issue.column ?? 'all'}`;
  return (
    <div className="q-find">
      <button type="button" className="q-find__btn" aria-expanded={open} aria-controls={panelId} onClick={toggle}>
        <div className="q-find__main">
          <div className="q-find__tags">
            <span className="q-find__kind mono">{info.kind}</span>
            {issue.column && <span className="mono q-find__col">{issue.column}</span>}
          </div>
          <div className="q-find__sum">{issue.message}</div>
        </div>
        <span className="mono q-find__count">
          {issue.count !== null ? countLabel(issue.count, info.unit) : ''} {open ? '−' : '+'}
        </span>
      </button>
      <div className="q-find__impact">
        <strong>Impact on analysis:</strong> {info.impact}
      </div>
      {open && (
        <div id={panelId} className="q-find__ex">
          <div className="eyebrow" style={{ marginBottom: 6 }}>
            Example affected rows (as uploaded)
          </div>
          {state.status === 'loading' && <div className="notice-box">Loading examples…</div>}
          {state.status === 'error' && (
            <p role="alert" className="inline-alert">
              Examples are unavailable: {state.message}
            </p>
          )}
          {state.status === 'ready' && !state.examples && <div className="notice-box">The server returned no example rows for this finding.</div>}
          {state.status === 'ready' && state.examples && (
            <div className="dtable" role="region" aria-label="Example rows (scrollable table)" tabIndex={0}>
              <table aria-label={`Example rows for ${info.kind}`}>
                <thead>
                  <tr>
                    {state.examples.columns.map((c) => (
                      <th key={c} scope="col" className="mono">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {state.examples.rows.map((r, i) => (
                    <tr key={i}>
                      {r.map((v, j) => (
                        <td key={j} className="mono" style={{ fontSize: 12.5 }}>
                          {v === null || v === '' ? '(empty)' : String(v)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
