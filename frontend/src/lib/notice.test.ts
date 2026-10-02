import { describe, expect, it } from 'vitest';
import { noticeFor } from './notice';

describe('analysis budget notice', () => {
  it('does not call a local analysis limit a provider outage', () => {
    const notice = noticeFor('budget_exhausted', 'The analysis reached its AI token budget.');
    expect(notice.variant).toBe('budget');
    expect(notice.title).toBe('This analysis needs a smaller question');
    expect(notice.body).toContain('token budget');
  });
});

describe('AI switched off by an admin', () => {
  it('is not presented as a provider outage', () => {
    const notice = noticeFor('ai_disabled', 'AI analysis is disabled. Dataset tools and saved results remain available.');
    expect(notice.variant).toBe('disabled');
    expect(notice.title).toBe('An admin has turned AI analysis off');
    expect(notice.body).toContain('AI analysis is disabled');
  });
});
