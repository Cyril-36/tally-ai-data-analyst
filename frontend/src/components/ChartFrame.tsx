import { useMemo, useState } from 'react';
import type { AnalysisResult, AnomalyFlag, ChartSpec, ResultTable, TimeGrain } from '../api/types';
import { buildFigure, chartTableAlternative } from '../lib/figure';
import { formatCell } from '../lib/format';
import { currencyFor } from '../lib/format';
import { PlotlyChart } from './PlotlyChart';

interface Props {
  table: ResultTable;
  chart: ChartSpec;
  result: Pick<AnalysisResult, 'claims'>;
  grain?: TimeGrain | null;
  flags?: AnomalyFlag[];
  mini?: boolean;
  height?: number;
  /** Shown to assistive tech, followed by a note that a table alternative follows. */
  label?: string;
}

/**
 * A chart plus its accessible alternative: the plot is role="img" with an aria-label, and a
 * visually hidden table lists the plotted values for screen readers.
 */
export function ChartFrame({ table, chart, result, grain, flags, mini = false, height, label }: Props) {
  const [failed, setFailed] = useState<string | null>(null);
  const figure = useMemo(() => {
    try {
      return buildFigure({ table, chart, result, grain, flags, mini });
    } catch (e) {
      return e instanceof Error ? e : new Error('Chart error');
    }
  }, [table, chart, result, grain, flags, mini]);
  const alt = useMemo(() => chartTableAlternative(table, chart), [table, chart]);

  if (figure instanceof Error || failed) {
    return (
      <div role="note" className="notice-box">
        <strong>Chart unavailable.</strong> {failed ?? (figure as Error).message} The result table is the source of truth.
      </div>
    );
  }
  return (
    <div>
      <PlotlyChart
        figure={figure}
        ariaLabel={`${label ?? chart.title}. The plotted values are listed in the table that follows.`}
        height={height ?? (mini ? 220 : 320)}
        onError={setFailed}
      />
      <table className="sr-only">
        <caption>{label ?? chart.title}</caption>
        <thead>
          <tr>
            {alt.headers.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {alt.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => {
                const col = table.columns.find((c) => c.name === alt.headers[j]);
                return <td key={j}>{col ? formatCell(cell, col, { currency: currencyFor(result, col.name), grain }) : String(cell)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
