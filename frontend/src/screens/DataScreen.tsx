import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { datasets as dsApi } from '../api/endpoints';
import type { DatasetDetail, DatasetMeta, RowsPage } from '../api/types';
import { Tabs } from '../components/Tabs';
import { UnavailableCard } from '../components/ThreadParts';
import { ago, formatTime, plural } from '../lib/format';
import { useModal } from '../state/modal';
import { useToast } from '../state/toast';
import { ConnectedDropZone, useUploads } from '../state/uploads';
import { useWorkspace } from '../state/workspace';

const PAGE = 25;
const ROLE_NOTE: Record<string, string> = {
  id: 'Identifier',
  time: 'Date or time',
  dimension: 'Category',
  metric: 'Measure',
};

export function DataScreen() {
  const { datasetId } = useParams();
  const ws = useWorkspace();
  const datasets = ws.datasets;
  const list = datasets ?? [];
  const current: DatasetMeta | undefined = datasetId ? list.find((d) => d.id === datasetId) : list[0];
  const foreignId = datasetId && datasets && !current ? datasetId : null;
  const { openDialog } = useUploads();
  const navigate = useNavigate();

  return (
    <div className="page">
      <div className="page__inner" style={{ maxWidth: 1160 }}>
        <div className="page__head">
          <div>
            <h2 className="page-title">Your data</h2>
            <p className="page-lede">
              {list.length
                ? `${plural(list.length, 'dataset')} · ${ws.selectedIds.length} selected for analysis · ${list.reduce((a, d) => a + d.row_count, 0).toLocaleString('en-GB')} rows`
                : datasets === null
                  ? 'Loading your datasets…'
                  : 'Nothing uploaded yet.'}
            </p>
          </div>
          <div className="page__head-actions">
            <button type="button" className="btn" onClick={() => void ws.loadSample()}>
              Load sample data
            </button>
            <button type="button" className="btn btn--primary" onClick={openDialog}>
              Upload CSV
            </button>
          </div>
        </div>

        {ws.datasetsError && <p role="alert" className="inline-alert">Could not load datasets: {ws.datasetsError}</p>}

        {datasets && list.length === 0 && !foreignId && (
          <ConnectedDropZone variant="band" label="Upload CSV files: drop files here or press Enter to choose">
            <div className="serif" style={{ fontSize: 28 }}>
              No datasets yet
            </div>
            <div style={{ fontSize: 13.5, color: 'var(--muted)', marginTop: 6 }}>
              Drop CSV files here or click to choose. You can also load the sample data.
            </div>
          </ConnectedDropZone>
        )}

        {list.length > 0 && (
          <Tabs
            label="Datasets"
            idPrefix="ds"
            className="ds-tabs"
            tabClassName="ds-tab"
            value={current?.id ?? ''}
            onChange={(id) => navigate(`/data/${id}`)}
            items={list.map((d) => ({ id: d.id, label: d.name }))}
            renderTab={(item) => {
              const d = list.find((x) => x.id === item.id) as DatasetMeta;
              const selected = ws.selectedIds.includes(d.id);
              return (
                <>
                  <span className="ds-tab__name">
                    <span className="ds-tab__dot" style={{ background: selected ? 'var(--accent)' : 'var(--border)' }} aria-hidden="true" />
                    {d.name}
                  </span>
                  <span className="ds-tab__meta">
                    {d.filename} · {d.row_count.toLocaleString('en-GB')} rows
                  </span>
                </>
              );
            }}
          />
        )}

        <div id="ds-panel" role="tabpanel" aria-labelledby={current ? `ds-tab-${current.id}` : undefined} style={{ display: 'contents' }}>
          {current && <DatasetPanel key={`${current.id}@${current.version}`} meta={current} />}
          {foreignId && <ForeignDataset id={foreignId} />}
        </div>
      </div>
    </div>
  );
}

function ForeignDataset({ id }: { id: string }) {
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    dsApi
      .detail(id)
      .then(() => setMessage('This dataset is not in your workspace.'))
      .catch((e) => setMessage(e instanceof ApiError ? e.message : 'This dataset could not be loaded.'));
  }, [id]);
  if (!message) return <div className="notice-box">Loading…</div>;
  return (
    <UnavailableCard
      message={message}
      action={
        <Link to="/data" className="btn btn--primary btn--sm">
          Back to your data
        </Link>
      }
    />
  );
}

function DatasetPanel({ meta }: { meta: DatasetMeta }) {
  const ws = useWorkspace();
  const toast = useToast();
  const { confirm } = useModal();
  const { replaceFile } = useUploads();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'preview' | 'columns'>('preview');
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(meta.name);
  const [renameError, setRenameError] = useState('');
  const renameInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    dsApi
      .detail(meta.id)
      .then((d) => live && setDetail(d))
      .catch((e) => live && setError(e instanceof ApiError ? e.message : 'This dataset could not be loaded.'));
    return () => {
      live = false;
    };
  }, [meta.id, meta.version]);

  useEffect(() => {
    if (renaming) renameInput.current?.select();
  }, [renaming]);

  const quality = ws.quality[ws.qualityKey(meta)];
  const issueCount = quality?.state === 'ready' ? quality.report.issues.length : null;
  const selected = ws.selectedIds.includes(meta.id);

  const commitRename = async () => {
    const name = draft.trim();
    if (!renaming) return;
    if (!name || name === meta.name) {
      setRenaming(false);
      setRenameError('');
      return;
    }
    try {
      const updated = await dsApi.rename(meta.id, name);
      ws.patchDataset(updated);
      setRenaming(false);
      setRenameError('');
    } catch (e) {
      setRenameError(e instanceof ApiError ? e.message : 'Could not rename this dataset.');
    }
  };

  const changeCurrency = async (value: string) => {
    try {
      const updated = await dsApi.setCurrency(meta.id, (value || null) as DatasetMeta['currency']);
      ws.patchDataset(updated);
      setRenameError('');
    } catch (e) {
      setRenameError(e instanceof ApiError ? e.message : 'Could not change the currency.');
    }
  };

  const askReplace = async () => {
    let results = 0;
    let pins = 0;
    try {
      ({ results, pins } = await dsApi.dependents(meta.id));
    } catch (e) {
      toast.toast(`Could not check what depends on this dataset: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
      return;
    }
    const ok = await confirm({
      title: `Replace ${meta.filename}?`,
      body: 'You’ll choose a new CSV file. The dataset keeps its name and selection; its preview, columns and quality findings are rebuilt from the new file.',
      effects: [
        pins ? `${plural(pins, 'dashboard item')} will be marked out of date until refreshed.` : 'No dashboard items use this dataset.',
        results ? `${plural(results, 'earlier answer')} keep their original results and will be labelled as based on the previous version.` : 'No earlier answers use this dataset.',
        'If the new file is rejected, nothing changes.',
      ],
      confirm: 'Choose replacement file',
    });
    if (ok) replaceFile(meta.id, meta.filename);
  };

  const askDelete = async () => {
    let results = 0;
    let pins = 0;
    try {
      ({ results, pins } = await dsApi.dependents(meta.id));
    } catch (e) {
      toast.toast(`Could not check what depends on this dataset: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
      return;
    }
    const ok = await confirm({
      title: `Delete ${meta.filename}?`,
      body: 'Tally’s copy of this file is removed. Your original file isn’t affected.',
      effects: [
        pins ? `${plural(pins, 'dashboard item')} will be marked “source deleted”.` : 'No dashboard items use this dataset.',
        results ? `${plural(results, 'earlier answer')} stay readable but can’t be refreshed.` : 'No earlier answers use this dataset.',
      ],
      confirm: 'Delete dataset',
      danger: true,
    });
    if (!ok) return;
    try {
      await dsApi.remove(meta.id);
      ws.dropDataset(meta.id);
      void ws.refreshPins();
      toast.toast(`${meta.filename} deleted from Tally. Dependent dashboard items are marked.`);
      navigate('/data');
    } catch (e) {
      toast.toast(`Could not delete: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
    }
  };

  const timeCols = detail?.profile.columns.filter((c) => c.role_hint === 'time') ?? [];
  const from = timeCols.map((c) => c.min).filter(Boolean).sort()[0];
  const to = timeCols.map((c) => c.max).filter(Boolean).sort().reverse()[0];

  if (error) {
    return (
      <UnavailableCard
        message={error}
        action={
          <Link to="/data" className="btn btn--primary btn--sm">
            Back to your data
          </Link>
        }
      />
    );
  }

  return (
    <section className="card ds-panel" aria-label={meta.name}>
      <div className="ds-panel__head">
        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {renaming ? (
            <>
              <input
                ref={renameInput}
                className="ds-panel__rename"
                value={draft}
                aria-label="Dataset name"
                maxLength={128}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => void commitRename()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void commitRename();
                  if (e.key === 'Escape') {
                    setRenaming(false);
                    setRenameError('');
                  }
                }}
              />
              {renameError && (
                <p role="alert" className="inline-alert">
                  {renameError}
                </p>
              )}
            </>
          ) : (
            <h3 className="serif ds-panel__name">{meta.name}</h3>
          )}
          <div className="mono ds-panel__file">
            {meta.filename} · updated {ago(meta.updated_at)}
          </div>
        </div>
        <div className="ds-panel__actions">
          <label className={`ds-panel__sel${selected ? ' ds-panel__sel--on' : ''}`}>
            <input type="checkbox" className="checkbox" checked={selected} onChange={() => ws.toggleSelected(meta.id)} />
            Selected for analysis
          </label>
          <button
            type="button"
            className="btn btn--sm"
            style={{ height: 34 }}
            onClick={() => {
              setDraft(meta.name);
              setRenaming(true);
            }}
          >
            Rename
          </button>
          <label className="ds-panel__sel" title="Money columns are shown in this currency. None = plain numbers.">
            Currency
            <select
              className="select"
              style={{ height: 34, width: 'auto' }}
              aria-label={`Currency of ${meta.name}`}
              value={meta.currency ?? ''}
              onChange={(e) => void changeCurrency(e.target.value)}
            >
              <option value="">None</option>
              <option value="GBP">GBP £</option>
              <option value="USD">USD $</option>
              <option value="EUR">EUR €</option>
            </select>
          </label>
          <button type="button" className="btn btn--sm" style={{ height: 34 }} onClick={() => void askReplace()}>
            Replace file…
          </button>
          <button type="button" className="btn btn--sm btn--danger-outline" style={{ height: 34 }} onClick={() => void askDelete()}>
            Delete…
          </button>
        </div>
      </div>

      <dl className="ds-stats">
        <Stat k="Rows" v={meta.row_count.toLocaleString('en-GB')} />
        <Stat k="Columns" v={detail ? String(detail.profile.columns.length) : '…'} />
        <Stat k="Date coverage" v={from && to ? `${formatTime(String(from))} – ${formatTime(String(to))}` : detail ? 'No date column' : '…'} />
        <Stat k="Version" v={`v${meta.version}`} />
        <div className="ds-stats__q" style={{ background: issueCount ? 'var(--warn-wash)' : 'var(--accent-faint)' }}>
          <Link to="/quality" className="ds-stats__qlink">
            <dt className="eyebrow">Quality findings</dt>
            <dd className="mono" style={{ color: issueCount ? 'var(--warn)' : 'var(--accent)' }}>
              {issueCount === null ? (quality?.state === 'error' ? 'Unavailable' : '…') : issueCount ? `${issueCount} finding${issueCount > 1 ? 's' : ''}` : 'None'} →
            </dd>
          </Link>
        </div>
      </dl>

      <Tabs
        label={`${meta.name} views`}
        idPrefix={`dv-${meta.id}`}
        className="ds-views"
        tabClassName="ds-view"
        value={tab}
        onChange={(id) => setTab(id as 'preview' | 'columns')}
        items={[
          { id: 'preview', label: 'Preview' },
          { id: 'columns', label: `Columns (${detail?.profile.columns.length ?? '…'})` },
        ]}
      />
      <div id={`dv-${meta.id}-panel`} role="tabpanel" aria-labelledby={`dv-${meta.id}-tab-${tab}`}>
        {!detail && <div className="ds-panel__pad notice-box" style={{ margin: 16 }}>Loading the dataset…</div>}
        {detail && tab === 'preview' && <PreviewTab detail={detail} meta={meta} />}
        {detail && tab === 'columns' && <ColumnsTab detail={detail} />}
      </div>
    </section>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="ds-stats__cell">
      <dt className="eyebrow">{k}</dt>
      <dd className="mono">{v}</dd>
    </div>
  );
}

function PreviewTab({ detail, meta }: { detail: DatasetDetail; meta: DatasetMeta }) {
  const [page, setPage] = useState(0);
  const [data, setData] = useState<RowsPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    dsApi
      .rows(meta.id, page * PAGE, PAGE)
      .then((r) => live && setData(r))
      .catch((e) => {
        if (!live) return;
        setError(e instanceof ApiError ? e.message : 'Could not load rows.');
        setData(null);
      });
    return () => {
      live = false;
    };
  }, [meta.id, meta.version, page]);

  // If the paging endpoint refuses, show the API's reason and the first rows from the detail response.
  const shown = data ?? (error ? { columns: detail.preview.columns, rows: detail.preview.rows, offset: 0, limit: detail.preview.rows.length, total: meta.row_count } : null);
  const total = shown?.total ?? meta.row_count;
  const offset = shown?.offset ?? page * PAGE;
  const end = Math.min(offset + (shown?.rows.length ?? 0), total);
  const types = useMemo(() => new Map(detail.profile.columns.map((c) => [c.name, c.dtype])), [detail]);

  return (
    <div className="ds-panel__pad">
      <div className="ds-prev__bar">
        <span className="mono" style={{ fontSize: 12, color: 'var(--muted)' }}>
          {shown && shown.rows.length ? `Rows ${(offset + 1).toLocaleString('en-GB')}–${end.toLocaleString('en-GB')} of ${total.toLocaleString('en-GB')}` : shown ? 'No rows' : 'Loading rows…'}
        </span>
        <div style={{ display: 'flex', gap: 6 }}>
          <button type="button" className="btn btn--sm" disabled={page === 0 || !!error} onClick={() => setPage((p) => Math.max(0, p - 1))}>
            ← Previous
          </button>
          <button type="button" className="btn btn--sm" disabled={!shown || end >= total || !!error} onClick={() => setPage((p) => p + 1)}>
            Next →
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="inline-warn">
          Paging is unavailable: {error.replace(/\.?$/, '.')} Showing the first {detail.preview.rows.length} rows instead.
        </p>
      )}
      {shown && (
        <div className="dtable ds-prev" style={{ maxHeight: 520 }} role="region" aria-label="Data preview (scrollable table)" tabIndex={0}>
          <table aria-label="Data preview">
            <thead>
              <tr>
                <th scope="col" className="ds-prev__n">
                  #
                </th>
                {shown.columns.map((c) => (
                  <th key={c} scope="col">
                    <div className="mono" style={{ fontSize: 12, color: 'var(--ink)' }}>
                      {c}
                    </div>
                    <div style={{ fontSize: 10.5, fontWeight: 400 }}>{types.get(c) ?? ''}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.rows.map((row, i) => (
                <tr key={i}>
                  <td className="ds-prev__n">{(offset + i + 1).toLocaleString('en-GB')}</td>
                  {row.map((cell, j) => (
                    <td key={j} className="mono ds-prev__c" style={cell === null || cell === '' ? { color: 'var(--warn)', background: '#fdf5e2' } : undefined}>
                      {cell === null || cell === '' ? '(empty)' : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ColumnsTab({ detail }: { detail: DatasetDetail }) {
  return (
    <div className="ds-panel__pad">
      <div className="dtable" role="region" aria-label="Columns (scrollable table)" tabIndex={0}>
        <table aria-label="Columns">
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Detected type</th>
              <th scope="col" className="num">
                Missing
              </th>
              <th scope="col">Example</th>
              <th scope="col">Notes</th>
            </tr>
          </thead>
          <tbody>
            {detail.profile.columns.map((c) => (
              <tr key={c.name}>
                <td className="mono" style={{ fontWeight: 500, fontSize: 12.5 }}>
                  {c.name}
                </td>
                <td>
                  <span className="type-badge mono">{c.dtype}</span>
                </td>
                <td className="num" style={{ color: c.null_count ? 'var(--warn)' : 'var(--muted)' }}>
                  {c.null_count.toLocaleString('en-GB')}
                </td>
                <td className="mono" style={{ color: 'var(--muted)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {c.sample_values[0] === undefined || c.sample_values[0] === null ? '—' : String(c.sample_values[0])}
                </td>
                <td style={{ color: 'var(--muted)' }}>{ROLE_NOTE[c.role_hint] ?? c.role_hint}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail.profile.warnings.length > 0 && (
        <ul className="ds-warnings">
          {detail.profile.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
