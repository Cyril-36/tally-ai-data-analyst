import { useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { auth } from '../api/endpoints';
import { useAuth } from '../state/auth';
import { useToast } from '../state/toast';
import { Dialog } from './Dialog';

export function AccountDialog({ onClose }: { onClose: () => void }) {
  const { user } = useAuth();
  const { toast } = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await auth.changePassword(current, next);
      toast('Password changed. Your other sessions were signed out.');
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change the password.');
      setBusy(false);
    }
  };

  return (
    <Dialog title="Account" onClose={onClose}>
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="expired__email">{user?.email}</div>
        <label className="field">
          Current password
          <input className="input" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="field">
          New password
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
            placeholder="At least 10 characters"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        {error && (
          <p role="alert" className="inline-alert">
            {error}
          </p>
        )}
        <div className="dialog__actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy || !current || !next}>
            Change password
          </button>
        </div>
      </form>
    </Dialog>
  );
}
