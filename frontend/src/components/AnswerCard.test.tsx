import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { clarifyResult, foundAnomalyResult, insufficientAnomalyResult, okResult, ORDERS, unverifiedResult } from '../test/fixtures';
import { AnomalyPanel } from './AnomalyPanel';
import { AnswerCard, type AnswerCardProps } from './AnswerCard';
import { ClarifyCard } from './ThreadParts';

// jsdom cannot draw Plotly; the chart wrapper only needs to expose its accessible name.
vi.mock('./PlotlyChart', () => ({
  PlotlyChart: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} data-testid="plot" />,
}));

function props(over: Partial<AnswerCardProps> = {}): AnswerCardProps {
  return {
    result: okResult,
    status: 'current',
    datasets: [ORDERS],
    pinned: false,
    actionsDisabled: false,
    detailsOpen: false,
    allRows: false,
    copied: false,
    onToggleDetails: vi.fn(),
    onToggleRows: vi.fn(),
    onCopySql: vi.fn(),
    onPin: vi.fn(),
    onExport: vi.fn(),
    onChartType: vi.fn(),
    onAsk: vi.fn(),
    ...over,
  };
}

describe('AnswerCard (outcome ok)', () => {
  it("renders the backend's sentences verbatim and nothing composed in the browser", () => {
    render(<AnswerCard {...props()} />);
    for (const s of okResult.answer_sentences) expect(screen.getByText(s)).toBeInTheDocument();
    expect(screen.getByText('✓ CALCULATION CHECKED')).toBeInTheDocument();
  });

  it('shows the table rows, caveats, chart (with a table alternative) and the question as the title', () => {
    render(<AnswerCard {...props()} />);
    expect(screen.getByRole('heading', { name: okResult.question })).toBeInTheDocument();
    const table = screen.getByRole('table', { name: okResult.question });
    expect(within(table).getByText('West')).toBeInTheDocument();
    expect(within(table).getByText('£135,306.20')).toBeInTheDocument();
    expect(screen.getByText(okResult.meta!.caveats[0].message)).toBeInTheDocument();
    expect(screen.getByTestId('plot')).toHaveAccessibleName(/Results by group.*table that follows/);
    expect(screen.getByRole('table', { name: /Results by group/, hidden: true })).toBeInTheDocument();
  });

  it('shows currency from the claim unit and never a hard-coded dollar sign', () => {
    const { container } = render(<AnswerCard {...props()} />);
    expect(screen.getAllByText(/£135,306\.20/).length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain('$');
  });

  it('lists SQL, bound parameters, source dataset versions and the trace in the details', () => {
    render(<AnswerCard {...props({ detailsOpen: true })} />);
    expect(screen.getByLabelText('SQL that ran')).toHaveTextContent('SUM((');
    expect(screen.getByText("?1 = 'West'")).toBeInTheDocument();
    expect(screen.getByText('orders (v2)')).toBeInTheDocument();
    expect(screen.getByText(/1 claim re-derived/)).toBeInTheDocument();
    expect(screen.getAllByText('run_analysis').length).toBeGreaterThan(0);
  });

  it('labels a result built on an earlier dataset version', () => {
    render(<AnswerCard {...props({ status: 'stale' })} />);
    expect(screen.getByText('Based on an earlier version of orders')).toBeInTheDocument();
  });

  it('wires pin, export, chart type and follow-up questions', async () => {
    const p = props();
    render(<AnswerCard {...p} />);
    await userEvent.click(screen.getByRole('button', { name: 'Pin to dashboard' }));
    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    await userEvent.click(screen.getByRole('button', { name: 'Export HTML report' }));
    await userEvent.click(screen.getByRole('button', { name: 'Compare these groups.' }));
    await userEvent.click(screen.getByRole('button', { name: 'pie' }));
    expect(p.onPin).toHaveBeenCalled();
    expect(p.onExport).toHaveBeenNthCalledWith(1, 'csv');
    expect(p.onExport).toHaveBeenNthCalledWith(2, 'html');
    expect(p.onAsk).toHaveBeenCalledWith('Compare these groups.');
    expect(p.onChartType).toHaveBeenCalledWith(expect.objectContaining({ type: 'pie', x: 'region', y: ['revenue'] }));
  });
});

describe('AnswerCard (narrative_unverified)', () => {
  it('withholds the summary, keeps table and chart, and never says "validated"', () => {
    const { container } = render(<AnswerCard {...props({ result: unverifiedResult })} />);
    expect(screen.getByText('Summary withheld.')).toBeInTheDocument();
    expect(screen.getByText(/could not be validated/)).toBeInTheDocument();
    expect(screen.getByRole('table', { name: unverifiedResult.question })).toBeInTheDocument();
    expect(screen.getByTestId('plot')).toBeInTheDocument();
    expect(screen.queryByText(/✓ CALCULATION CHECKED/)).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\bVALIDATED\b/);
    expect(screen.queryByText(okResult.answer_sentences[0])).not.toBeInTheDocument();
  });
});

describe('clarification', () => {
  it('shows the question and option buttons that ask the follow-up', async () => {
    const onPick = vi.fn();
    render(<ClarifyCard clarification={clarifyResult.clarification!} disabled={false} onPick={onPick} />);
    expect(screen.getByText('Which metric or criterion should I use?')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'quantity' }));
    expect(onPick).toHaveBeenCalledWith('quantity');
  });

  it('marks the answered option and disables the rest', () => {
    render(<ClarifyCard clarification={clarifyResult.clarification!} chosen="revenue" disabled={false} onPick={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'revenue' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'quantity' })).toBeDisabled();
    expect(screen.getByText('Answered: “revenue”')).toBeInTheDocument();
  });
});

describe('anomaly evidence', () => {
  it('insufficient_data shows its reason and flags nothing', () => {
    render(<AnomalyPanel report={insufficientAnomalyResult.anomalies!} result={insufficientAnomalyResult} />);
    expect(screen.getByRole('status')).toHaveTextContent('Insufficient data');
    expect(screen.getByRole('status')).toHaveTextContent('1 constant (no variation)');
    expect(screen.queryByText(/ABOVE TYPICAL|BELOW TYPICAL/)).not.toBeInTheDocument();
    expect(screen.queryByText(/unusual value/)).not.toBeInTheDocument();
  });

  it('a found flag shows median, MAD, robust z, threshold and its reference population', async () => {
    render(<AnomalyPanel report={foundAnomalyResult.anomalies!} result={foundAnomalyResult} methodOpen />);
    expect(screen.getByText('£18,846.06')).toBeInTheDocument();
    expect(screen.getByText('£6,144.04')).toBeInTheDocument();
    expect(screen.getByText(/MAD £700\.50 · robust z 12\.2 · threshold 3\.5/)).toBeInTheDocument();
    expect(screen.getByText('Compared with: own history of revenue (21 points over period)')).toBeInTheDocument();
    expect(screen.getByText('ABOVE TYPICAL')).toBeInTheDocument();
    expect(screen.getByText('Jan 2026')).toBeInTheDocument();
    expect(screen.getByText(/1 checked · 0 skipped/)).toBeInTheDocument();
  });

  it('is shown inside the answer card when the result carries an anomaly report', () => {
    render(<AnswerCard {...props({ result: insufficientAnomalyResult })} />);
    expect(screen.getByRole('region', { name: 'Anomaly check' })).toHaveTextContent('Insufficient data');
  });
});
