import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { exportResult } from '../api/endpoints';
import type { AnomalyFlag } from '../api/types';
import { AnswerCard } from '../components/AnswerCard';
import { Spinner } from '../components/Spinner';
import { ClarifyCard, NoticeCard, RunningCard, UnavailableCard, UserBubble } from '../components/ThreadParts';
import { noticeFor } from '../lib/notice';
import { contextItems, deriveStatus, entityLabel } from '../lib/resultView';
import { truncate } from '../lib/format';
import { useAuth } from '../state/auth';
import { useThreads, type ThreadEntry } from '../state/threads';
import { useToast } from '../state/toast';
import { ConnectedDropZone } from '../state/uploads';
import { useWorkspace } from '../state/workspace';

// Static product copy: examples of the kinds of question that work, not data-derived suggestions.
const CAPABILITIES = [
  { k: 'Totals & rankings', q: 'Which region generated the highest revenue?' },
  { k: 'Trends over time', q: 'Show monthly sales trends' },
  { k: 'Across related files', q: 'Who are the top five customers?' },
  { k: 'Unusual values', q: 'Detect unusual sales values' },
];

export function AskScreen() {
  const { conversationId = null } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const ws = useWorkspace();
  const threads = useThreads();
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [highlight, setHighlight] = useState<string | null>(null);

  const entries: ThreadEntry[] = useMemo(() => (conversationId ? threads.threads[conversationId] ?? [] : []), [conversationId, threads.threads]);
  const loadError = conversationId ? threads.threadErrors[conversationId] : undefined;
  const { loadThread } = threads;

  useEffect(() => {
    if (conversationId) void loadThread(conversationId);
  }, [conversationId, loadThread]);

  // "Open analysis" from the dashboard passes the result to scroll to.
  const target = (location.state as { highlight?: string } | null)?.highlight;
  useEffect(() => {
    if (!target) return;
    setHighlight(target);
    const t = window.setTimeout(() => setHighlight(null), 2600);
    return () => window.clearTimeout(t);
  }, [target]);

  const lastStepCount = entries[entries.length - 1]?.steps.length ?? 0;
  useLayoutEffect(() => {
    const box = scroller.current;
    if (!box) return;
    if (highlight) {
      const el = document.getElementById(`result-${highlight}`);
      if (el) {
        box.scrollTop = Math.max(0, el.offsetTop - 24);
        return;
      }
    }
    box.scrollTop = box.scrollHeight;
  }, [entries.length, lastStepCount, highlight, conversationId, entries]);

  const datasets = ws.datasets;
  const selected = (datasets ?? []).filter((d) => ws.selectedIds.includes(d.id));
  const lastResult = [...entries].reverse().find((e) => e.result?.spec && e.result.outcome === 'ok')?.result;
  const running = threads.running;
  const runningEntry = entries.find((e) => e.status === 'running');

  const send = useCallback(
    (text: string) => {
      void threads.ask(text, conversationId);
    },
    [threads, conversationId],
  );

  const edit = (question: string) => {
    ws.setDraft(question);
    window.setTimeout(() => textarea.current?.focus(), 40);
  };

  const hasDatasets = (datasets?.length ?? 0) > 0;
  const showOnboarding = !conversationId && datasets !== null && !hasDatasets;
  const showStarter = !conversationId && hasDatasets;
  const showComposer = hasDatasets || !!conversationId;
  const name = (user?.email ?? '').split('@')[0];

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };
  useEffect(() => {
    if (textarea.current) grow(textarea.current);
  }, [ws.draft]);

  return (
    <div className="ask">
      <div className="ask__thread" ref={scroller}>
        <div className="ask__inner">
          {showOnboarding && (
            <div className="onboard">
              <div className="onboard__intro">
                <span className="eyebrow">Welcome, {name}</span>
                <h2 className="onboard__title">Start with a CSV file — or with ours.</h2>
                <p className="onboard__lede">
                  Tally reads your data, answers questions in plain language, and shows the calculation behind every number. Your files are never
                  edited.
                </p>
              </div>
              <div className="onboard__tiles">
                <ConnectedDropZone variant="tile">
                  <span className="eyebrow eyebrow--accent">Your data</span>
                  <div>
                    <div className="tile__title">Drop CSV files here</div>
                    <div className="tile__sub">or click to choose · several at once</div>
                  </div>
                </ConnectedDropZone>
                <button type="button" className="tile tile--sample" onClick={() => void ws.loadSample()}>
                  <span className="eyebrow">Sample data</span>
                  <div>
                    <div className="tile__title">Try a retail sample</div>
                    <div className="tile__sub">Orders, customers, products and payments for a fictional retailer, in GBP.</div>
                  </div>
                </button>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <span className="eyebrow">What you can ask</span>
                <div className="caps">
                  {CAPABILITIES.map((c) => (
                    <div key={c.k}>
                      <span className="caps__k">{c.k}</span>
                      <span className="caps__q">“{c.q}”</span>
                    </div>
                  ))}
                </div>
                <p className="onboard__fine">
                  Not supported yet: forecasting, editing or cleaning your source data, and questions about data you haven’t uploaded.
                </p>
              </div>
            </div>
          )}

          {showStarter && (
            <div className="starter">
              <h2 className="starter__title">What would you like to know?</h2>
              <p className="starter__lede">
                Asking about <span className="mono">{selected.length ? selected.map((d) => d.filename).join(', ') : 'no datasets (select one in the sidebar)'}</span>.
                Follow-up suggestions appear under each answer, drawn from the result’s own columns.
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }} role="group" aria-label="Examples to try">
                {CAPABILITIES.map((c) => (
                  <button key={c.q} type="button" className="suggest" disabled={running} onClick={() => send(c.q)}>
                    <span className="suggest__arrow">→</span>
                    <span>{c.q}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {loadError && (
            <UnavailableCard
              message={loadError}
              action={
                <Link to="/ask" className="btn btn--primary btn--sm">
                  Start a new question
                </Link>
              }
            />
          )}

          {entries.map((entry) => (
            <EntryView key={entry.id} entry={entry} conversationId={conversationId as string} highlighted={highlight === entry.result?.id} onEdit={edit} />
          ))}
        </div>
      </div>

      {showComposer && (
        <div className="composer">
          <div className="composer__inner">
            {lastResult && (
              <div className="composer__ctx" aria-label="Active analytical context">
                <span className="eyebrow eyebrow--accent">“That” means</span>
                <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{truncate(lastResult.question, 80)}</span>
                {contextItems(lastResult.spec)
                  .filter((c) => ['Data', 'Filters', 'Period'].includes(c.k))
                  .map((c) => (
                    <span key={c.k}>
                      {c.k} <span className="composer__ctx-v">{c.v}</span>
                    </span>
                  ))}
                <button type="button" className="link-btn link-btn--underline" onClick={() => navigate('/ask')}>
                  Start fresh
                </button>
              </div>
            )}
            <div className={`composer__box${threads.needSelect ? ' composer__box--err' : ''}`}>
              <div className="composer__chips">
                <span>Asking about</span>
                {selected.map((d) => (
                  <span key={d.id} className="chip">
                    {d.filename}
                    <button type="button" aria-label={`Stop using ${d.filename}`} onClick={() => ws.toggleSelected(d.id)}>
                      ×
                    </button>
                  </span>
                ))}
                {selected.length === 0 && <span style={{ color: 'var(--error)' }}>no dataset selected</span>}
                <button type="button" className="link-btn" onClick={() => navigate('/data')}>
                  Change
                </button>
              </div>
              <div className="composer__row">
                <textarea
                  ref={textarea}
                  rows={1}
                  value={ws.draft}
                  aria-label="Ask a question about your data"
                  placeholder={lastResult ? 'Ask a follow-up — e.g. “Now only for West”' : 'Ask a question about your data…'}
                  onChange={(e) => {
                    ws.setDraft(e.target.value);
                    threads.clearNeedSelect();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      if (!running) send(ws.draft);
                    }
                  }}
                />
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={running || !ws.draft.trim()}
                  onClick={() => send(ws.draft)}
                  style={{ flex: '0 0 auto' }}
                >
                  {running ? 'Working…' : 'Ask'}
                </button>
              </div>
            </div>
            {threads.needSelect && (
              <div role="alert" className="composer__need">
                Select at least one dataset before asking — questions are answered from the selected files only.
                <button type="button" onClick={() => navigate('/data')}>
                  Choose datasets
                </button>
              </div>
            )}
            <div className="composer__status" aria-live="polite">
              {running && <Spinner />}
              <span>
                {running
                  ? `Working — ${(runningEntry?.steps[runningEntry.steps.length - 1]?.label ?? 'sending').toLowerCase()}. The answer appears after validation.`
                  : 'Enter to send · Shift+Enter for a new line'}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface EntryProps {
  entry: ThreadEntry;
  conversationId: string;
  highlighted: boolean;
  onEdit: (question: string) => void;
}

function EntryView({ entry, conversationId, highlighted, onEdit }: EntryProps) {
  const ws = useWorkspace();
  const threads = useThreads();
  const { toast, patch } = useToast();
  const navigate = useNavigate();
  const result = entry.result;
  const running = threads.running;
  const datasets = ws.datasets ?? [];

  const doExport = useCallback(
    (format: 'csv' | 'html') => {
      if (!result) return;
      const label = format === 'csv' ? 'CSV' : 'HTML report';
      const id = toast(`Preparing ${label} from stored result ${result.id.slice(0, 8)}…`, { sticky: true, busy: true });
      exportResult(result.id, format)
        .then((name) => patch(id, { text: `Downloaded ${name}`, busy: false }))
        .catch((e) =>
          patch(id, {
            text: `${label} export failed: ${e instanceof ApiError ? e.message : 'unexpected error'}`,
            tone: 'error',
            busy: false,
            sticky: true,
            action: 'Retry',
            onAction: () => doExport(format),
          }),
        );
    },
    [patch, result, toast],
  );

  const copySql = (sql: string) => {
    const done = () => {
      threads.patchEntry(conversationId, entry.id, (e) => ({ ...e, ui: { ...e.ui, copied: true } }));
      window.setTimeout(() => threads.patchEntry(conversationId, entry.id, (e) => ({ ...e, ui: { ...e.ui, copied: false } })), 1600);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(sql).then(done, () => toast('Could not access the clipboard. Select the SQL and copy it manually.', { tone: 'error' }));
    else toast('Clipboard unavailable. Select the SQL and copy it manually.');
  };

  const ui = (patchUi: Partial<ThreadEntry['ui']>) => threads.patchEntry(conversationId, entry.id, (e) => ({ ...e, ui: { ...e.ui, ...patchUi } }));

  let body: React.ReactNode = null;
  if (entry.status === 'running') {
    body = <RunningCard steps={entry.steps} />;
  } else if (entry.error) {
    body = (
      <NoticeCard
        copy={noticeFor(entry.error.outcome, entry.error.message)}
        question={entry.question}
        disabled={running}
        onRetry={() => void threads.ask(entry.question, conversationId, entry)}
        onEdit={() => onEdit(entry.question)}
        onAsk={(q) => void threads.ask(q, conversationId)}
      />
    );
  } else if (result) {
    const askMore = (q: string) => void threads.ask(q, conversationId);
    if (result.outcome === 'needs_clarification' && result.clarification) {
      body = <ClarifyCard clarification={result.clarification} chosen={entry.chosen} disabled={running} onPick={askMore} />;
    } else if (result.outcome === 'ok' || result.outcome === 'narrative_unverified') {
      if (result.table && result.table.rows.length === 0) {
        body = (
          <NoticeCard
            copy={noticeFor('ok', result.message, true)}
            question={entry.question}
            suggestions={result.suggested_questions}
            disabled={running}
            onRetry={() => undefined}
            onEdit={() => onEdit(entry.question)}
            onAsk={askMore}
          />
        );
      } else {
        const status = deriveStatus(result, ws.datasets, entry.apiStatus);
        body = (
          <AnswerCard
            result={result}
            status={status}
            datasets={datasets}
            domId={`result-${result.id}`}
            highlighted={highlighted}
            seconds={entry.finishedAt && entry.startedAt && entry.id.startsWith('q') ? (entry.finishedAt - entry.startedAt) / 1000 : undefined}
            pinned={!!ws.pins?.some((p) => p.result_id === result.id)}
            actionsDisabled={running}
            detailsOpen={!!entry.ui.details}
            allRows={!!entry.ui.allRows}
            copied={!!entry.ui.copied}
            onToggleDetails={() => ui({ details: !entry.ui.details })}
            onToggleRows={() => ui({ allRows: !entry.ui.allRows })}
            onCopySql={() => result.meta && copySql(result.meta.sql)}
            onPin={() => {
              if (ws.pins?.some((p) => p.result_id === result.id)) navigate('/dashboard');
              else void ws.pinResult(result);
            }}
            onExport={doExport}
            onChartType={(chart) => void threads.changeChart(conversationId, entry, chart)}
            onAsk={askMore}
            onAskDrive={(flag: AnomalyFlag) => {
              const grain = result.spec?.time_bucket;
              askMore(`What drove ${flag.metric.replace(/_/g, ' ')} for ${entityLabel(flag.row_key, grain)}?`);
            }}
          />
        );
      }
    } else {
      body = (
        <NoticeCard
          copy={noticeFor(result.outcome, result.message)}
          question={entry.question}
          suggestions={result.suggested_questions}
          disabled={running}
          onRetry={() => void threads.ask(entry.question, conversationId, entry)}
          onEdit={() => onEdit(entry.question)}
          onAsk={(q) => void threads.ask(q, conversationId)}
        />
      );
    }
  }

  return (
    <>
      <UserBubble text={entry.question} />
      {body}
    </>
  );
}
