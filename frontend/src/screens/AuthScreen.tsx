import { useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { Tabs } from '../components/Tabs';
import { useAuth } from '../state/auth';

type Mode = 'signin' | 'signup';

export function AuthScreen() {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await (mode === 'signin' ? signIn(email.trim(), password) : signUp(email.trim(), password));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.');
      setBusy(false);
    }
  };

  const signin = mode === 'signin';
  return (
    <div className="auth">
      <section className="auth__brand">
        <div className="auth__logo">
          <span className="wordmark">Tally</span>
          <span className="auth__badge">AI DATA ANALYST</span>
        </div>
        <div>
          <h1 className="auth__headline">Ask your spreadsheets a question. Check every answer.</h1>
          <p className="auth__lede">
            Upload CSV files and ask in plain language. Each answer comes with the chart, the table and the exact calculation behind it.
          </p>
        </div>
        <div className="auth__sample" aria-label="Example of an answer">
          <div className="auth__sample-tag">EXAMPLE · NOT YOUR DATA</div>
          <div className="auth__sample-q">Every figure in an answer is read from a stored calculation, not typed by a model.</div>
          <div>DATA orders.csv · METRIC SUM(revenue) · SQL AND PARAMETERS SHOWN</div>
        </div>
      </section>
      <section className="auth__panel">
        <form className="auth__form" onSubmit={submit} noValidate={false}>
          <Tabs
            label="Account"
            idPrefix="auth"
            className="auth__tabs"
            value={mode}
            onChange={(id) => {
              setMode(id as Mode);
              setError('');
            }}
            items={[
              { id: 'signin', label: 'Sign in' },
              { id: 'signup', label: 'Create account' },
            ]}
          />
          <div id="auth-panel" role="tabpanel" aria-labelledby={`auth-tab-${mode}`} style={{ display: 'contents' }}>
            <h2 className="auth__h">{signin ? 'Welcome back' : 'Create your workspace'}</h2>
            <label className="field">
              Email
              <input
                className="input"
                type="email"
                name="email"
                autoComplete="email"
                required
                value={email}
                placeholder="you@company.com"
                onChange={(e) => {
                  setEmail(e.target.value);
                  setError('');
                }}
              />
            </label>
            <label className="field">
              Password
              <input
                className="input"
                type="password"
                name="password"
                autoComplete={signin ? 'current-password' : 'new-password'}
                required
                minLength={signin ? undefined : 10}
                value={password}
                placeholder={signin ? 'Your password' : 'At least 10 characters'}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setError('');
                }}
              />
            </label>
            {error && (
              <p role="alert" className="inline-alert">
                {error}
              </p>
            )}
            <button type="submit" className="btn btn--primary auth__submit" disabled={busy}>
              {busy ? (signin ? 'Signing in…' : 'Creating account…') : signin ? 'Sign in' : 'Create account'}
            </button>
            <p className="auth__fine">Each account sees only its own datasets, conversations and dashboards.</p>
          </div>
        </form>
      </section>
    </div>
  );
}
