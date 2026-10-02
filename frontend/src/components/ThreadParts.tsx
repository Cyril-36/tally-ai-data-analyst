import type { Clarification } from '../api/types';
import type { NoticeCopy } from '../lib/notice';
import type { Step } from '../state/threads';
import { Spinner } from './Spinner';

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="bubble-row">
      <div className="bubble">{text}</div>
    </div>
  );
}

export function RunningCard({ steps }: { steps: Step[] }) {
  return (
    <div className="running" aria-live="polite" role="status">
      <span className="eyebrow">Working · the answer appears once it’s validated</span>
      {steps.length === 0 && (
        <div className="running__step">
          <Spinner />
          <span>Sending the question</span>
        </div>
      )}
      {steps.map((s) => (
        <div key={s.id} className="running__step">
          {s.state === 'active' ? <Spinner /> : <span className="running__check">✓</span>}
          <span className="running__label">{s.label}</span>
          {s.detail && <span className="mono running__detail">{s.detail}</span>}
        </div>
      ))}
    </div>
  );
}

interface ClarifyProps {
  clarification: Clarification;
  chosen?: string;
  disabled: boolean;
  onPick: (option: string) => void;
}

export function ClarifyCard({ clarification, chosen, disabled, onPick }: ClarifyProps) {
  return (
    <article className="card clarify">
      <span className="eyebrow eyebrow--accent">Needs clarification</span>
      <p className="serif clarify__q">{clarification.question}</p>
      {clarification.options.length > 0 && (
        <div className="clarify__opts" role="group" aria-label="Options">
          {clarification.options.map((o) => (
            <button
              key={o}
              type="button"
              className={`opt${chosen === o ? ' opt--chosen' : ''}`}
              disabled={disabled || !!chosen}
              aria-pressed={chosen === o}
              onClick={() => onPick(o)}
            >
              {o}
            </button>
          ))}
        </div>
      )}
      <div className="clarify__hint">{chosen ? `Answered: “${chosen}”` : 'Pick one, or type your own answer below.'}</div>
    </article>
  );
}

/** A resource the API refused (404 for another user's conversation, deleted, etc.). */
export function UnavailableCard({ message, action }: { message: string; action: React.ReactNode }) {
  return (
    <article className="card notice" role="group" aria-label="Unavailable">
      <div>
        <span className="tag">Unavailable</span>
      </div>
      <h3 className="serif notice__title">This isn’t available to you</h3>
      <p className="notice__body">{message}</p>
      <div className="notice__actions">{action}</div>
    </article>
  );
}

interface NoticeProps {
  copy: NoticeCopy;
  question: string;
  suggestions?: string[];
  disabled: boolean;
  onRetry: () => void;
  onEdit: () => void;
  onAsk: (q: string) => void;
}

export function NoticeCard({ copy, question, suggestions = [], disabled, onRetry, onEdit, onAsk }: NoticeProps) {
  return (
    <article className="card notice" role="group" aria-label={copy.title}>
      <div>
        <span className={`tag${copy.tone === 'warn' ? ' tag--warn' : copy.tone === 'error' ? ' tag--error' : ''}`}>{copy.tag}</span>
      </div>
      <h3 className="serif notice__title">{copy.title}</h3>
      <p className="notice__body">{copy.body}</p>
      {copy.next && (
        <p className="notice__next">
          <strong>Next:</strong> {copy.next}
        </p>
      )}
      <div className="notice__q">
        Your question: <span>“{question}”</span>
      </div>
      <div className="notice__actions">
        {copy.actions.includes('retry') && (
          <button type="button" className="btn btn--primary btn--sm" disabled={disabled} onClick={onRetry}>
            Retry
          </button>
        )}
        {copy.actions.includes('edit') && (
          <button type="button" className="btn btn--primary btn--sm" disabled={disabled} onClick={onEdit}>
            Edit question
          </button>
        )}
        {suggestions.map((q) => (
          <button key={q} type="button" className="pill" disabled={disabled} onClick={() => onAsk(q)}>
            {q}
          </button>
        ))}
      </div>
    </article>
  );
}
