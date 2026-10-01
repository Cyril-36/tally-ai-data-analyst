import { useEffect, useRef, useState } from 'react';
import { Outlet, useLocation, useMatch } from 'react-router-dom';
import { ApiError } from '../api/client';
import { exportConversation } from '../api/endpoints';
import { useMediaQuery } from '../lib/useMediaQuery';
import { useThreads } from '../state/threads';
import { useToast } from '../state/toast';
import { useWorkspace } from '../state/workspace';
import { Sidebar } from './Sidebar';

const TITLES: Record<string, string> = {
  '/data': 'Data',
  '/quality': 'Data quality',
  '/anomalies': 'Anomalies',
  '/dashboard': 'Dashboard',
  '/settings': 'Admin settings',
};

export function AppShell() {
  const narrow = useMediaQuery('(max-width: 899px)');
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const convMatch = useMatch('/ask/:conversationId');
  const ws = useWorkspace();
  const threads = useThreads();
  const { toast } = useToast();
  const toggle = useRef<HTMLButtonElement>(null);
  const sidebarId = 'workspace-sidebar';

  useEffect(() => setNavOpen(false), [location.pathname]);
  useEffect(() => {
    if (!narrow) setNavOpen(false);
  }, [narrow]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setNavOpen(false);
        toggle.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    document.getElementById(sidebarId)?.querySelector<HTMLElement>('a, button')?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [navOpen]);

  const convId = convMatch?.params.conversationId;
  const conv = ws.conversations.find((c) => c.id === convId);
  const topLevel = '/' + location.pathname.split('/')[1];
  const title = topLevel === '/ask' ? (conv?.title ?? 'New question') : (TITLES[topLevel] ?? 'Tally');
  const canExport = !!convId && (threads.threads[convId]?.some((e) => e.result) ?? false);

  const showSidebar = !narrow || navOpen;
  return (
    <div className="app">
      {narrow && navOpen && <div className="app__scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />}
      {showSidebar && <Sidebar id={sidebarId} drawer={narrow} onNavigate={() => setNavOpen(false)} />}
      <main className="main" id="main">
        <header className="header">
          {narrow && (
            <button
              ref={toggle}
              type="button"
              className="icon-btn"
              aria-label="Open menu"
              aria-expanded={navOpen}
              aria-controls={sidebarId}
              onClick={() => setNavOpen(true)}
            >
              ☰
            </button>
          )}
          <h1>{title}</h1>
          <span className="header__spacer" />
          {canExport && convId && (
            <button
              type="button"
              className="btn btn--sm"
              onClick={() =>
                exportConversation(convId)
                  .then((name) => toast(`Downloaded ${name}`))
                  .catch((e) => toast(`Export failed: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 }))
              }
            >
              Export conversation
            </button>
          )}
        </header>
        <Outlet />
      </main>
    </div>
  );
}
