import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { ORDERS } from './test/fixtures';

type Handler = (init: RequestInit) => { status?: number; body?: unknown };
type Routes = Record<string, Handler | { status?: number; body?: unknown }>;

/** A tiny in-memory API: "METHOD /path" -> response. Unknown calls fail the test loudly. */
function mockApi(routes: Routes) {
  const calls: Array<{ key: string; init: RequestInit }> = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://localhost');
    const key = `${init.method ?? 'GET'} ${url.pathname}`;
    calls.push({ key, init });
    const route = routes[key];
    if (!route) throw new Error(`Unexpected request: ${key}`);
    const { status = 200, body = {} } = typeof route === 'function' ? route(init) : route;
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fn);
  return { calls, fn };
}

const SIGNED_OUT = { status: 401, body: { outcome: 'permission_denied', message: 'Please sign in.' } };
const quiet: Routes = {
  'GET /api/datasets': { body: [] },
  'GET /api/conversations': { body: [] },
  'GET /api/dashboard': { body: [] },
};

function mountApp(path = '/ask') {
  return render(
    <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => vi.useRealTimers());
afterEach(() => vi.unstubAllGlobals());

describe('401 handling', () => {
  it('shows the sign-in screen, not the workspace, when /api/auth/me answers 401', async () => {
    mockApi({ 'GET /api/auth/me': SIGNED_OUT });
    mountApp();
    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();
  });

  it('wrong credentials stay on the sign-in screen with the API message', async () => {
    mockApi({
      'GET /api/auth/me': SIGNED_OUT,
      'POST /api/auth/login': { status: 401, body: { outcome: 'permission_denied', message: 'Invalid email or password.' } },
    });
    mountApp();
    await userEvent.type(await screen.findByLabelText('Email'), 'a@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'nope-nope-nope');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.');
    expect(screen.queryByText('Session expired')).not.toBeInTheDocument();
  });

  it('a 401 mid-session hides the workspace behind a sign-in, keeps the typed question, and restores it after signing in again', async () => {
    const api = mockApi({
      ...quiet,
      'GET /api/auth/me': { body: { id: 'u1', email: 'analyst@example.com', role: 'analyst' } },
      'GET /api/datasets': { body: [ORDERS] },
      'GET /api/datasets/ds-orders/quality': { body: { score: 100, penalties: {}, row_count: 1, reference_date: '2026-10-02', columns: [], duplicate_rows: 0, candidate_keys: [], issues: [] } },
      // creating the conversation for the first question is where the session turns out to be gone
      'POST /api/conversations': SIGNED_OUT,
      'POST /api/auth/login': { body: { id: 'u1', email: 'analyst@example.com', role: 'analyst' } },
    });
    mountApp();
    const box = await screen.findByLabelText('Ask a question about your data');
    await userEvent.type(box, 'Which region generated the highest revenue?');
    await userEvent.keyboard('{Enter}');

    const dialog = await screen.findByRole('dialog', { name: 'Sign in to continue' });
    expect(within(dialog).getByText('analyst@example.com')).toBeInTheDocument();
    // the workspace is hidden, not destroyed
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText('Password'), 'correct-horse-battery');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Sign in to continue' })).not.toBeInTheDocument());
    expect(await screen.findByLabelText('Ask a question about your data')).toHaveValue('Which region generated the highest revenue?');
    expect(api.calls.some((c) => c.key === 'POST /api/auth/login')).toBe(true);
  });

  it('sends X-Requested-With on state-changing requests and cookies on all', async () => {
    const api = mockApi({
      'GET /api/auth/me': SIGNED_OUT,
      'POST /api/auth/login': { status: 401, body: { outcome: 'permission_denied', message: 'Invalid email or password.' } },
    });
    mountApp();
    await userEvent.type(await screen.findByLabelText('Email'), 'a@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'x');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await screen.findByRole('alert');
    const me = api.calls.find((c) => c.key === 'GET /api/auth/me')!;
    const login = api.calls.find((c) => c.key === 'POST /api/auth/login')!;
    expect(me.init.credentials).toBe('include');
    expect(login.init.credentials).toBe('include');
    expect((login.init.headers as Record<string, string>)['X-Requested-With']).toBe('fetch');
    expect((me.init.headers as Record<string, string>)['X-Requested-With']).toBeUndefined();
  });
});

describe('admin gating', () => {
  const adminRoutes: Routes = {
    ...quiet,
    'GET /api/admin/settings': { body: { max_upload_mb: 50, llm_enabled: true, sample_data_enabled: true, retention_days: 90 } },
    'GET /api/ready': { body: { status: 'ready', ai_configured: true } },
  };

  it('shows the admin item and settings (with the AI switch) only when /me says role === "admin"', async () => {
    mockApi({ ...adminRoutes, 'GET /api/auth/me': { body: { id: 'a1', email: 'boss@example.com', role: 'admin' } } });
    mountApp('/settings');
    expect(await screen.findByRole('heading', { name: 'Admin settings', level: 2 })).toBeInTheDocument();
    expect(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: /Admin settings/ })).toBeInTheDocument();
    expect(await screen.findByRole('switch', { name: /AI analysis/ })).toBeChecked();
  });

  it('never infers admin from the email: an analyst called admin@example.com gets no admin UI and is redirected away', async () => {
    const api = mockApi({ ...adminRoutes, 'GET /api/auth/me': { body: { id: 'u9', email: 'admin@example.com', role: 'analyst' } } });
    mountApp('/settings');
    await screen.findByRole('navigation', { name: 'Main' });
    expect(within(screen.getByRole('navigation', { name: 'Main' })).queryByRole('link', { name: /Admin settings/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Admin settings', level: 2 })).not.toBeInTheDocument();
    expect(api.calls.some((c) => c.key === 'GET /api/admin/settings')).toBe(false);
  });
});
