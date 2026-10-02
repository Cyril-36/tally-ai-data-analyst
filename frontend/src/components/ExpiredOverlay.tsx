import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { useAuth } from '../state/auth';
import { useToast } from '../state/toast';

/**
 * Shown when the API answers 401 while the workspace is open. The workspace stays mounted
 * (hidden) so the typed question is still there after signing in again.
 */
export function ExpiredOverlay() {
  const { user, signIn, signOut } = useAuth();
  const { toast } = useToast();
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => field.current?.focus(), []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!user || busy) return;
    setBusy(true);
    setError('');
    try {
      const again = await signIn(user.email, password);
      toast(again.id === user.id ? 'Signed in again. Your question is still in the box.' : 'Signed in.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.');
      setBusy(false);
    }
  };

  return (
    <div className="expired" role="dialog" aria-modal="true" aria-labelledby="exp-h">
      <form className="expired__form" onSubmit={submit}>
        <span className="eyebrow eyebrow--error">Session expired</span>
        <h2 id="exp-h">Sign in to continue</h2>
        <p style={{ fontSize: 14, lineHeight: 1.5, color: 'var(--muted)' }}>
          Your workspace is hidden until you sign in again. Your unsent question will be waiting.
        </p>
        <div className="expired__email">{user?.email}</div>
        <label className="field">
          Password
          <input ref={field} className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && (
          <p role="alert" className="inline-alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn--primary auth__submit" disabled={busy}>
          Sign in
        </button>
        <button type="button" className="btn btn--quiet" style={{ color: 'var(--muted)', textDecoration: 'underline' }} onClick={() => void signOut()}>
          Use a different account
        </button>
      </form>
    </div>
  );
}
