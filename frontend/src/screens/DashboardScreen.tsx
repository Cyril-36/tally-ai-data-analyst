import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { conversations as convApi, exportResult, pins as pinsApi } from '../api/endpoints';
import type { DashboardItem } from '../api/types';
import { ChartFrame } from '../components/ChartFrame';
import { currencyFor, formatCell, shortDate, shortId } from '../lib/format';
import { contextItems, sourceVersions } from '../lib/resultView';
import { useToast } from '../state/toast';
import { useWorkspace } from '../state/workspace';

export function DashboardScreen() {
  const ws = useWorkspace();
  const navigate = useNavigate();
  const { toast, patch } = useToast();
  const [items, setItems] = useState<DashboardItem[] | null>(ws.pins);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [dragId, setDragId] = useState<string | null>(null);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const { refreshPins } = ws;

  useEffect(() => {
    void refreshPins();
  }, [refreshPins]);
  useEffect(() => setItems(ws.pins), [ws.pins]);

  const persistOrder = useCallback(
    async (next: DashboardItem[]) => {
      setItems(next);
      try {
        await Promise.all(next.map((p, i) => (p.position === i ? null : pinsApi.update(p.id, { position: i }))));
      } catch (e) {
        toast(`Could not save the new order: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
      }
      await refreshPins();
    },
    [refreshPins, toast],
  );

  const move = (id: string, delta: number) => {
    if (!items) return;
    const from = items.findIndex((p) => p.id === id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= items.length) return;
    const next = items.slice();
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    void persistOrder(next);
  };

  const drop = (target: string) => {
    if (!items || !dragId || dragId === target) return;
    const next = items.slice();
    const from = next.findIndex((p) => p.id === dragId);
    const to = next.findIndex((p) => p.id === target);
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setDragId(null);
    void persistOrder(next);
  };

  const commitRename = async (item: DashboardItem) => {
    const title = draft.trim();
    setRenaming(null);
    if (!title || title === item.title) return;
    try {
      await pinsApi.update(item.id, { title });
      await refreshPins();
    } catch (e) {
      toast(`Could not rename: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
    }
  };

  const remove = async (item: DashboardItem, index: number) => {
    const label = item.title || item.result.question;
    try {
      await pinsApi.remove(item.id);
      await refreshPins();
      toast(`Removed “${label}”.`, {
        action: 'Undo',
        ms: 8000,
        onAction: () => {
          pinsApi
            .create(item.result_id, `undo:${crypto.randomUUID()}`)
            .then((pin) => pinsApi.update(pin.id, { title: item.title, position: index }))
            .then(() => refreshPins())
            .catch((e) => toast(`Could not restore it: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 }));
        },
      });
    } catch (e) {
      toast(`Could not remove: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
    }
  };

  // Refresh = re-ask the stored question in its conversation, then point the card at the new result.
  const refresh = async (item: DashboardItem, index: number) => {
    const ids = Object.keys(item.result.meta?.dataset_versions ?? {}).filter((id) => ws.datasets?.some((d) => d.id === id));
    setBusy((s) => new Set(s).add(item.id));
    const label = item.title || item.result.question;
    try {
      const fresh = await convApi.askOnce(item.result.conversation_id, {
        question: item.result.question,
        dataset_ids: ids,
        request_key: crypto.randomUUID(),
      });
      if (fresh.outcome !== 'ok') {
        toast(`Couldn’t refresh “${label}”: ${fresh.message ?? fresh.outcome.replace(/_/g, ' ')}. It stays marked out of date.`, { tone: 'error', ms: 8000 });
        return;
      }
      const pin = await pinsApi.create(fresh.id, `pin:${fresh.id}`);
      await pinsApi.update(pin.id, { title: item.title, position: index });
      await pinsApi.remove(item.id);
      await refreshPins();
      toast(`Refreshed “${label}” from current data (new result ${shortId(fresh.id)}).`);
    } catch (e) {
      toast(`Couldn’t refresh “${label}”: ${e instanceof ApiError ? e.message : 'unexpected error'}. It stays marked out of date.`, { tone: 'error', ms: 8000 });
    } finally {
      setBusy((s) => {
        const next = new Set(s);
        next.delete(item.id);
        return next;
      });
    }
  };

  const doExport = (item: DashboardItem, format: 'csv' | 'html') => {
    const id = toast(`Preparing ${format === 'csv' ? 'CSV' : 'HTML report'}…`, { sticky: true, busy: true });
    exportResult(item.result_id, format)
      .then((name) => patch(id, { text: `Downloaded ${name}`, busy: false }))
      .catch((e) =>
        patch(id, {
          text: `Export failed: ${e instanceof ApiError ? e.message : 'unexpected error'}`,
          tone: 'error',
          busy: false,
          sticky: true,
          action: 'Retry',
          onAction: () => doExport(item, format),
        }),
      );
  };

  return (
    <div className="page">
      <div className="page__inner" style={{ maxWidth: 1280 }}>
        <div>
          <h2 className="page-title">Dashboard</h2>
          <p className="page-lede">Pinned results keep their own data, filters and period. Drag cards, or use the arrows, to reorder.</p>
        </div>
        {items && items.length === 0 && (
          <div className="empty-box" style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center', padding: 36 }}>
            <div className="serif" style={{ fontSize: 26, color: 'var(--ink)' }}>
              Nothing pinned yet
            </div>
            <div style={{ fontSize: 13.5 }}>Use “Pin to dashboard” on any answer to keep it here.</div>
            <button type="button" className="btn btn--primary" style={{ marginTop: 6 }} onClick={() => navigate('/ask')}>
              Ask a question
            </button>
          </div>
        )}
        {items === null && <div className="notice-box">Loading your dashboard…</div>}
        <ul className="pins">
          {(items ?? []).map((item, i) => (
            <PinCard
              key={item.id}
              item={item}
              index={i}
              count={items?.length ?? 0}
              renaming={renaming === item.id}
              draft={draft}
              setDraft={setDraft}
              dragging={dragId === item.id}
              busy={busy.has(item.id)}
              onStartRename={() => {
                setDraft(item.title || item.result.question);
                setRenaming(item.id);
              }}
              onCommitRename={() => void commitRename(item)}
              onCancelRename={() => setRenaming(null)}
              onMove={(d) => move(item.id, d)}
              onDragStart={() => setDragId(item.id)}
              onDrop={() => drop(item.id)}
              onDragEnd={() => setDragId(null)}
              onOpen={() => navigate(`/ask/${item.result.conversation_id}`, { state: { highlight: item.result_id } })}
              onExport={(f) => doExport(item, f)}
              onRemove={() => void remove(item, i)}
              onRefresh={() => void refresh(item, i)}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

interface PinCardProps {
  item: DashboardItem;
  index: number;
  count: number;
  renaming: boolean;
  draft: string;
  setDraft: (v: string) => void;
  dragging: boolean;
  busy: boolean;
  onStartRename: () => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onMove: (delta: number) => void;
  onDragStart: () => void;
  onDrop: () => void;
  onDragEnd: () => void;
  onOpen: () => void;
  onExport: (format: 'csv' | 'html') => void;
  onRemove: () => void;
  onRefresh: () => void;
}

function PinCard(p: PinCardProps) {
  const ws = useWorkspace();
  const { item } = p;
  const result = item.result;
  const title = item.title || result.question;
  const stale = item.status !== 'current';
  const missing = item.status === 'missing';
  const sources = sourceVersions(result, ws.datasets ?? []);
  const ctx = contextItems(result.spec);
  const grain = result.spec?.time_bucket?.grain ?? null;
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (p.renaming) input.current?.select();
  }, [p.renaming]);

  const first = result.table?.columns[0];
  const firstMetric = result.table?.columns.findIndex((c) => c.role === 'metric') ?? -1;

  return (
    <li
      className={`card pin${stale ? (missing ? ' pin--missing' : ' pin--stale') : ''}`}
      draggable
      aria-label={title}
      style={{ opacity: p.dragging ? 0.5 : 1 }}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move';
        try {
          e.dataTransfer.setData('text/plain', item.id);
        } catch {
          /* some browsers refuse */
        }
        p.onDragStart();
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        p.onDrop();
      }}
      onDragEnd={p.onDragEnd}
    >
      <div className="pin__head">
        <span aria-hidden="true" className="pin__grip">
          ⋮⋮
        </span>
        {p.renaming ? (
          <input
            ref={input}
            className="pin__rename"
            value={p.draft}
            aria-label="Item title"
            onChange={(e) => p.setDraft(e.target.value)}
            onBlur={p.onCommitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') p.onCommitRename();
              if (e.key === 'Escape') p.onCancelRename();
            }}
            autoFocus
          />
        ) : (
          <h3 className="pin__title">{title}</h3>
        )}
        <div style={{ display: 'flex', gap: 2 }}>
          <button type="button" className="pin__arrow" aria-label={`Move “${title}” earlier`} disabled={p.index === 0} onClick={() => p.onMove(-1)}>
            ↑
          </button>
          <button type="button" className="pin__arrow" aria-label={`Move “${title}” later`} disabled={p.index === p.count - 1} onClick={() => p.onMove(1)}>
            ↓
          </button>
        </div>
      </div>

      {stale && (
        <div role="status" className={`pin__stale${missing ? ' pin__stale--missing' : ''}`}>
          <div>
            <strong>{missing ? 'Source deleted.' : 'Out of date.'}</strong>{' '}
            {missing
              ? `A dataset behind this result no longer exists, so it can’t be refreshed. It shows what was true on ${shortDate(result.created_at)}.`
              : `${sources.filter((s) => !s.missing).map((s) => s.name).join(', ') || 'A source'} was replaced after this result was calculated. Values below reflect the previous version.`}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {!missing && (
              <button type="button" disabled={p.busy} onClick={p.onRefresh}>
                {p.busy ? 'Refreshing…' : 'Re-run with current data'}
              </button>
            )}
            <button type="button" onClick={p.onRemove}>
              Remove item
            </button>
          </div>
        </div>
      )}

      <div className="pin__body" style={{ opacity: stale ? 0.55 : 1 }}>
        {result.outcome === 'ok' && result.answer_sentences[0] && <p className="serif pin__conclusion">{result.answer_sentences[0]}</p>}
        {result.outcome === 'narrative_unverified' && (
          <p className="pin__withheld">Summary withheld. The table and chart come straight from the calculation.</p>
        )}
        {result.table && result.chart ? (
          <ChartFrame table={result.table} chart={result.chart} result={result} grain={grain} mini label={title} />
        ) : (
          result.table &&
          first && (
            <div className="pin__mini">
              {result.table.rows.slice(0, 5).map((row, i) => (
                <div key={i} style={{ display: 'contents' }}>
                  <div className="pin__mini-k">{formatCell(row[0], first, { grain })}</div>
                  <div className="pin__mini-v mono">
                    {firstMetric >= 0 ? formatCell(row[firstMetric], result.table!.columns[firstMetric], { currency: currencyFor(result, result.table!.columns[firstMetric].name) }) : ''}
                  </div>
                </div>
              ))}
            </div>
          )
        )}
      </div>

      <dl className="pin__meta">
        <dt>Sources</dt>
        <dd className="mono">{sources.length ? sources.map((s) => `${s.name} v${s.version}`).join(' + ') : '—'}</dd>
        <dt>Filters</dt>
        <dd className="mono">{ctx.find((c) => c.k === 'Filters')?.v ?? '—'}</dd>
        <dt>Period</dt>
        <dd className="mono">{ctx.find((c) => c.k === 'Period')?.v ?? '—'}</dd>
        <dt>Result</dt>
        <dd className="mono">
          {shortId(result.id)} · {result.outcome === 'ok' ? 'validated' : 'calculated'} {shortDate(result.created_at)}
        </dd>
      </dl>

      <div className="pin__actions">
        <button type="button" className="btn btn--link btn--sm" onClick={p.onOpen}>
          Open analysis
        </button>
        <button type="button" className="btn btn--quiet btn--sm" onClick={() => p.onExport('csv')}>
          CSV
        </button>
        <button type="button" className="btn btn--quiet btn--sm" onClick={() => p.onExport('html')}>
          Report
        </button>
        <button type="button" className="btn btn--quiet btn--sm" onClick={p.onStartRename}>
          Rename
        </button>
        <span style={{ flex: 1 }} />
        <button type="button" className="btn btn--quiet btn--sm" style={{ color: 'var(--error)' }} onClick={p.onRemove}>
          Remove
        </button>
      </div>
    </li>
  );
}
