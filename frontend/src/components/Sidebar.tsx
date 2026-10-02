import { useState } from 'react';
import { Link, NavLink, useMatch, useNavigate } from 'react-router-dom';
import { ago, plural } from '../lib/format';
import { useAuth } from '../state/auth';
import { useModal } from '../state/modal';
import { useThreads } from '../state/threads';
import { useUploads } from '../state/uploads';
import { useWorkspace } from '../state/workspace';
import { AccountDialog } from './AccountDialog';
import { Dialog } from './Dialog';
import { Menu } from './Menu';

interface Props {
  id: string;
  drawer: boolean;
  onNavigate: () => void;
}

export function Sidebar({ id, drawer, onNavigate }: Props) {
  const { user, isAdmin, signOut } = useAuth();
  const ws = useWorkspace();
  const threads = useThreads();
  const { openDialog } = useUploads();
  const { confirm } = useModal();
  const navigate = useNavigate();
  const dataMatch = useMatch('/data/:datasetId');
  const convMatch = useMatch('/ask/:conversationId');
  const [accountOpen, setAccountOpen] = useState(false);
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);

  const datasets = ws.datasets ?? [];
  const nav: Array<[string, string, number]> = [
    ['/ask', 'Ask', 0],
    ['/data', 'Data', datasets.length],
    ['/quality', 'Data quality', ws.qualityFindingCount],
    ['/anomalies', 'Anomalies', 0],
    ['/dashboard', 'Dashboard', ws.pins?.length ?? 0],
    ...(isAdmin ? ([['/settings', 'Admin settings', 0]] as Array<[string, string, number]>) : []),
  ];

  const askDelete = async (convId: string, title: string) => {
    const ok = await confirm({
      title: 'Delete this conversation?',
      body: `“${title}” and its saved results will be deleted.`,
      effects: ['Dashboard items pinned from this conversation are removed too.', 'Your datasets are not affected.'],
      confirm: 'Delete conversation',
      danger: true,
    });
    if (ok) await threads.deleteConversation(convId);
  };

  return (
    <aside id={id} aria-label="Workspace" className={`side${drawer ? ' side--drawer' : ''}`}>
      <div className="side__top">
        <span className="wordmark">Tally</span>
        <button
          type="button"
          className="side__new"
          onClick={() => {
            navigate('/ask');
            onNavigate();
          }}
        >
          + New question
        </button>
      </div>
      <nav aria-label="Main" className="side__nav">
        {nav.map(([to, label, count]) => (
          <NavLink key={to} to={to} onClick={onNavigate} className={undefined}>
            <span>{label}</span>
            {count > 0 && <span className="side__count">{count}</span>}
          </NavLink>
        ))}
      </nav>
      <div className="side__scroll">
        <div className="side__group">
          <div className="side__group-head">
            <span className="eyebrow">Datasets</span>
            <button type="button" className="btn btn--quiet" onClick={() => { openDialog(); onNavigate(); }}>
              Upload
            </button>
          </div>
          {datasets.length > 0 ? (
            <>
              <div className="side__hint">
                {ws.selectedIds.length} of {datasets.length} selected for analysis
              </div>
              {datasets.map((d) => (
                <div key={d.id} className={`ds-row${dataMatch?.params.datasetId === d.id ? ' ds-row--active' : ''}`}>
                  <input
                    type="checkbox"
                    className="checkbox"
                    checked={ws.selectedIds.includes(d.id)}
                    onChange={() => ws.toggleSelected(d.id)}
                    aria-label={`Use ${d.filename} for analysis`}
                  />
                  <Link to={`/data/${d.id}`} className="ds-row__open" onClick={onNavigate}>
                    <span className="ds-row__name">{d.name}</span>
                    <span className="ds-row__meta">
                      {d.filename} · {d.row_count.toLocaleString('en-GB')} rows
                    </span>
                  </Link>
                </div>
              ))}
            </>
          ) : (
            ws.datasets && (
              <div className="side__empty">
                No data yet.{' '}
                <button type="button" onClick={() => { void ws.loadSample(); onNavigate(); }}>
                  Load sample data
                </button>
              </div>
            )
          )}
        </div>
        <div className="side__group side__group--tight">
          <span className="eyebrow" style={{ padding: '0 6px 4px' }}>
            Conversations
          </span>
          {ws.conversations.map((c) => {
            const active = convMatch?.params.conversationId === c.id;
            const count = threads.threads[c.id]?.length;
            return (
              <div key={c.id} className={`conv${active ? ' conv--active' : ''}`}>
                <Link to={`/ask/${c.id}`} className="conv__link" aria-current={active ? 'page' : undefined} onClick={onNavigate}>
                  <span className="conv__title">{c.title}</span>
                  <span className="conv__meta">
                    {ago(c.created_at)}
                    {count !== undefined && count > 0 ? ` · ${plural(count, 'question')}` : ''}
                  </span>
                </Link>
                <div className="conv__menu">
                  <Menu
                    label={`Actions for ${c.title}`}
                    trigger={<span aria-hidden="true">⋯</span>}
                    items={[
                      { label: 'Rename', onSelect: () => setRenaming({ id: c.id, title: c.title }) },
                      { label: 'Delete…', danger: true, onSelect: () => void askDelete(c.id, c.title) },
                    ]}
                  />
                </div>
              </div>
            );
          })}
          {ws.conversations.length === 0 && <div className="side__empty" style={{ padding: '0 6px' }}>Your conversations will appear here.</div>}
        </div>
      </div>
      <div className="side__user">
        <button type="button" className="side__avatar" aria-label="Account and password" onClick={() => setAccountOpen(true)}>
          {(user?.email ?? '?')[0].toUpperCase()}
        </button>
        <button type="button" className="side__who" onClick={() => setAccountOpen(true)}>
          <div className="side__email">{user?.email}</div>
          <div className="side__role">{isAdmin ? 'Admin' : 'Member'}</div>
        </button>
        <button type="button" className="side__signout" onClick={() => void signOut()}>
          Sign out
        </button>
      </div>
      {accountOpen && <AccountDialog onClose={() => setAccountOpen(false)} />}
      {renaming && (
        <RenameDialog
          initial={renaming.title}
          onClose={() => setRenaming(null)}
          onSave={async (title) => {
            await threads.renameConversation(renaming.id, title);
            setRenaming(null);
          }}
        />
      )}
    </aside>
  );
}

function RenameDialog({ initial, onSave, onClose }: { initial: string; onSave: (title: string) => Promise<void>; onClose: () => void }) {
  const [title, setTitle] = useState(initial);
  return (
    <Dialog title="Rename conversation" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) void onSave(title.trim());
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
      >
        <label className="field">
          Title
          <input className="input" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="dialog__actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn--primary" disabled={!title.trim()}>
            Save
          </button>
        </div>
      </form>
    </Dialog>
  );
}
