import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { admin } from '../api/endpoints';
import type { SystemSettings } from '../api/types';
import { useToast } from '../state/toast';

const LIMITS = [25, 50, 100];
const RETENTION: Array<{ label: string; value: number | null }> = [
  { label: '30 days', value: 30 },
  { label: '90 days', value: 90 },
  { label: '1 year', value: 365 },
  { label: 'Never', value: null },
];

export function SettingsScreen() {
  const { toast } = useToast();
  const [saved, setSaved] = useState<SystemSettings | null>(null);
  const [draft, setDraft] = useState<SystemSettings | null>(null);
  const [error, setError] = useState('');
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    admin
      .getSettings()
      .then((s) => {
        if (!live) return;
        const full = { sample_data_enabled: true, retention_days: 90, ...s };
        setSaved(full);
        setDraft(full);
      })
      .catch((e) => live && setError(e instanceof ApiError ? e.message : 'Settings could not be loaded.'));
    admin
      .ready()
      .then((r) => live && setConfigured(!!r.ai_configured))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const test = async () => {
    setTesting(true);
    try {
      const r = await admin.testProvider();
      toast(r.ok ? `AI provider responded in ${r.latency_ms} ms (${r.model}).` : 'The AI provider did not respond correctly.', r.ok ? {} : { tone: 'error', ms: 7000 });
    } catch (e) {
      toast(`Provider test failed: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 8000 });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const out = await admin.putSettings(draft);
      const full = { ...draft, ...out };
      setSaved(full);
      setDraft(full);
      setError('');
      toast('Settings saved.');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Settings could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  const limits = draft && !LIMITS.includes(draft.max_upload_mb) ? [...LIMITS, draft.max_upload_mb].sort((a, b) => a - b) : LIMITS;
  const dirty = !!draft && !!saved && JSON.stringify(draft) !== JSON.stringify(saved);
  const status = !draft ? '…' : !draft.llm_enabled ? 'SWITCHED OFF' : configured === null ? 'CHECKING' : configured ? 'KEY CONFIGURED' : 'NOT CONFIGURED';
  const statusOk = !!draft?.llm_enabled && !!configured;

  return (
    <div className="page">
      <div className="page__inner" style={{ maxWidth: 760 }}>
        <div>
          <h2 className="page-title">Admin settings</h2>
          <p className="page-lede">Application-wide settings. Admin access does not include other users’ datasets, conversations or dashboards.</p>
        </div>
        {error && (
          <p role="alert" className="inline-alert">
            {error}
          </p>
        )}
        {draft && (
          <section className="card settings">
            <div className="settings__row">
              <div>
                <div className="settings__t">AI provider</div>
                <div className="settings__d">Interprets questions. Calculations run separately.</div>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span className={`tag ${statusOk ? 'tag--ok' : 'tag--warn'}`} role="status">
                  {status}
                </span>
                <button type="button" className="btn btn--sm" disabled={testing} onClick={() => void test()}>
                  {testing ? 'Testing…' : 'Test connection'}
                </button>
              </div>
            </div>
            <label className="settings__row">
              <div>
                <div className="settings__t">AI analysis</div>
                <div className="settings__d">Switch off to stop every AI call. Uploads, data quality, anomalies and the dashboard keep working.</div>
              </div>
              <input
                type="checkbox"
                role="switch"
                className="checkbox settings__check"
                aria-checked={draft.llm_enabled}
                checked={draft.llm_enabled}
                onChange={(e) => setDraft({ ...draft, llm_enabled: e.target.checked })}
              />
            </label>
            <label className="settings__row">
              <div>
                <div className="settings__t">Maximum upload size</div>
                <div className="settings__d">Per CSV file.</div>
              </div>
              <select className="select" value={draft.max_upload_mb} onChange={(e) => setDraft({ ...draft, max_upload_mb: Number(e.target.value) })}>
                {limits.map((n) => (
                  <option key={n} value={n}>
                    {n} MB
                  </option>
                ))}
              </select>
            </label>
            <label className="settings__row">
              <div>
                <div className="settings__t">Offer sample data</div>
                <div className="settings__d">Shows “Try a retail sample” and allows loading it. Off refuses sample requests.</div>
              </div>
              <input
                type="checkbox"
                className="checkbox settings__check"
                checked={!!draft.sample_data_enabled}
                onChange={(e) => setDraft({ ...draft, sample_data_enabled: e.target.checked })}
              />
            </label>
            <label className="settings__row settings__row--last">
              <div>
                <div className="settings__t">Conversation retention</div>
                <div className="settings__d">Older conversations and their results are deleted, along with pins on them. Datasets are kept.</div>
              </div>
              <select
                className="select"
                value={draft.retention_days === null || draft.retention_days === undefined ? 'never' : String(draft.retention_days)}
                onChange={(e) => setDraft({ ...draft, retention_days: e.target.value === 'never' ? null : Number(e.target.value) })}
              >
                {RETENTION.map((r) => (
                  <option key={r.label} value={r.value === null ? 'never' : String(r.value)}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
          </section>
        )}
        {!draft && !error && <div className="notice-box">Loading settings…</div>}
        {draft && (
          <div>
            <button type="button" className="btn btn--primary" style={{ height: 38, padding: '0 16px' }} disabled={saving || !dirty} onClick={() => void save()}>
              {saving ? 'Saving…' : 'Save settings'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
