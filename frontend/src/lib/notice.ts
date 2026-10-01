/** Maps an API outcome to the prototype's notice variants. The body text is always the API's own message. */
import type { Outcome } from '../api/types';

export type NoticeVariant =
  | 'noRows'
  | 'missing'
  | 'unsupported'
  | 'refused'
  | 'failed'
  | 'provider'
  | 'forbidden'
  | 'invalid'
  | 'network'
  | 'interrupted';

export interface NoticeCopy {
  variant: NoticeVariant;
  tag: string;
  tone: 'neutral' | 'warn' | 'error';
  title: string;
  body: string;
  next?: string;
  actions: Array<'retry' | 'edit'>;
}

const DEFAULTS: Record<NoticeVariant, Omit<NoticeCopy, 'variant' | 'body'> & { body: string }> = {
  noRows: {
    tag: 'No matching rows',
    tone: 'neutral',
    title: 'No rows matched your filters',
    body: 'The calculation ran, but no rows matched.',
    next: 'Loosen a filter or choose a different period.',
    actions: ['edit'],
  },
  missing: {
    tag: 'Not enough data',
    tone: 'warn',
    title: 'There is not enough data to answer this',
    body: 'The selected data does not contain what is needed.',
    next: 'Select other datasets or upload the missing data.',
    actions: ['edit'],
  },
  unsupported: {
    tag: 'Not supported',
    tone: 'neutral',
    title: 'This kind of question is not supported yet',
    body: 'This analysis is not supported yet.',
    next: 'Rephrase it as a total, ranking, trend, comparison or unusual-value check.',
    actions: ['edit'],
  },
  refused: {
    tag: 'Calculation refused',
    tone: 'warn',
    title: 'Tally declined to run this calculation',
    body: 'The calculation was refused.',
    next: 'Ask about each file separately, or choose a different relationship.',
    actions: ['edit'],
  },
  failed: {
    tag: 'Analysis failed',
    tone: 'error',
    title: 'Something went wrong',
    body: 'The analysis could not be completed.',
    next: 'Retry or edit the question.',
    actions: ['retry', 'edit'],
  },
  provider: {
    tag: 'AI service unavailable',
    tone: 'error',
    title: 'The AI service could not answer',
    body: 'The AI provider failed or is switched off.',
    next: 'Retry shortly. Uploads, data quality, anomalies and the dashboard keep working.',
    actions: ['retry', 'edit'],
  },
  forbidden: {
    tag: 'Unavailable',
    tone: 'neutral',
    title: 'This is not available to you',
    body: 'It was not found, or you do not have access to it.',
    actions: ['edit'],
  },
  invalid: {
    tag: 'Not accepted',
    tone: 'warn',
    title: 'That request could not be processed',
    body: 'The request was not accepted.',
    next: 'Edit the question or the dataset selection and try again.',
    actions: ['retry', 'edit'],
  },
  network: {
    tag: 'Connection problem',
    tone: 'error',
    title: 'The connection dropped',
    body: 'Could not reach the server.',
    next: 'Retry. The same request key is reused, so nothing runs twice.',
    actions: ['retry', 'edit'],
  },
  interrupted: {
    tag: 'Interrupted',
    tone: 'warn',
    title: 'This request was interrupted',
    body: 'The page closed before the answer was finished, so nothing was shown or saved.',
    next: 'Run it again.',
    actions: ['retry', 'edit'],
  },
};

const REFUSAL = /\b(join|fan-?out|double[- ]count|many-to-many|one-to-many|duplicate keys?)\b/i;

export function variantFor(outcome: Outcome | 'network', message: string | null | undefined, emptyTable = false): NoticeVariant {
  switch (outcome) {
    case 'unsupported':
      return message && REFUSAL.test(message) ? 'refused' : 'unsupported';
    case 'insufficient_data':
    case 'needs_clarification':
      return 'missing';
    case 'execution_failed':
      return 'failed';
    case 'provider_failed':
      return 'provider';
    case 'permission_denied':
    case 'not_found':
      return 'forbidden';
    case 'invalid_input':
      return 'invalid';
    case 'network':
      return 'network';
    case 'ok':
      return emptyTable ? 'noRows' : 'failed';
    default:
      return 'failed';
  }
}

export function noticeFor(outcome: Outcome | 'network', message: string | null | undefined, emptyTable = false): NoticeCopy {
  const variant = variantFor(outcome, message, emptyTable);
  const d = DEFAULTS[variant];
  return { ...d, variant, body: message?.trim() ? message : d.body };
}

export function interruptedNotice(): NoticeCopy {
  return { variant: 'interrupted', ...DEFAULTS.interrupted };
}
