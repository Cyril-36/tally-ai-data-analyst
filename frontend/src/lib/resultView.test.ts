import { describe, expect, it } from 'vitest';
import { ORDERS, okResult } from '../test/fixtures';
import { currencyChanges, deriveStatus, sourceVersions, staleLabel } from './resultView';

const calculated = {
  ...okResult,
  meta: { ...okResult.meta!, dataset_versions: { [ORDERS.id]: ORDERS.version }, source_currencies: { [ORDERS.id]: 'GBP' } },
};

describe('result staleness', () => {
  it('is current while version and declared currency are unchanged', () => {
    expect(deriveStatus(calculated, [ORDERS])).toBe('current');
  });

  it('is out of date after the currency declaration changes, and says so', () => {
    const now = [{ ...ORDERS, currency: 'USD' as const }];
    expect(deriveStatus(calculated, now)).toBe('stale');
    const label = staleLabel('stale', sourceVersions(calculated, now), currencyChanges(calculated, now));
    expect(label).toBe('orders currency changed from GBP to USD');
  });

  it('a renamed dataset is not a currency change', () => {
    expect(deriveStatus(calculated, [{ ...ORDERS, name: 'sales' }])).toBe('current');
  });

  it('a new file version is still reported as a replacement', () => {
    const now = [{ ...ORDERS, version: ORDERS.version + 1 }];
    expect(staleLabel('stale', sourceVersions(calculated, now), currencyChanges(calculated, now))).toBe(
      'Based on an earlier version of orders',
    );
  });
});
