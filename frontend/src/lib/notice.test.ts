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
