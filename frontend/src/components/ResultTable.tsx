import { useState } from 'react';
import type { AnalysisResult } from '../api/types';
import { currencyFor, formatCell } from '../lib/format';

interface Props {
  result: AnalysisResult;
  label: string;
  /** Rows shown before "Show all"; the dashboard / answer card use 10. */
  initialRows?: number;
  expanded?: boolean;
  onToggle?: () => void;
}

export function ResultTable({ result, label, initialRows = 10, expanded, onToggle }: Props) {
  const [localAll, setLocalAll] = useState(false);
  const table = result.table;
  if (!table) return null;
  const all = expanded ?? localAll;
  const toggle = onToggle ?? (() => setLocalAll((v) => !v));
  const grain = result.spec?.time_bucket?.grain ?? null;
  const shown = all ? table.rows : table.rows.slice(0, initialRows);
  // A metric column with any fractional value shows two decimals throughout, so figures line up.
  const decimals = table.columns.map((c, j) =>
    c.role === 'metric' && table.rows.some((r) => typeof r[j] === 'number' && !Number.isInteger(r[j] as number)) ? 2 : undefined,
  );
  const total = table.rows.length;
  const note = [
    shown.length < total ? `showing ${shown.length} of ${total} rows` : `${total} row${total === 1 ? '' : 's'}`,
    result.meta?.truncated ? 'capped at the row limit; the full result is larger' : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="rtable">
      <div className="rtable__head">
        <span className="eyebrow">Result table</span>
        <span className="mono rtable__note">{note}</span>
      </div>
      <div className="dtable" style={{ maxHeight: 360 }} tabIndex={0} role="region" aria-label={`${label} (scrollable table)`}>
        <table aria-label={label}>
          <thead>
            <tr>
              {table.columns.map((c) => (
                <th key={c.name} scope="col" className={c.role === 'metric' ? 'num' : undefined}>
                  {c.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i}>
                {table.columns.map((c, j) => (
                  <td key={c.name} className={c.role === 'metric' ? 'num' : undefined}>
                    {formatCell(row[j], c, { currency: currencyFor(result, c.name), grain, decimals: decimals[j] })}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > initialRows && (
        <button type="button" className="btn btn--link btn--xs" style={{ marginTop: 6 }} onClick={toggle}>
          {all ? 'Show fewer rows' : `Show all ${total} rows`}
        </button>
      )}
    </div>
  );
}
