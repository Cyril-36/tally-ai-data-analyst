import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { signIn, signUpFresh, uploadSamples } from './helpers';

const TRANSFER = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../evals/transfer');

test.describe('automatic overview dashboard', () => {
  test('retail sample: GBP totals, three drawn charts, and a card can be pinned', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['orders.csv']);
    await page.goto('/dashboard');
    await page.getByLabel('Dataset for the overview').selectOption({ label: 'orders' });
    await page.getByRole('button', { name: 'Generate overview' }).click();
    const kpis = page.getByRole('list', { name: /Headline totals/ }).or(page.locator('dl.overview__kpis'));
    await expect(kpis).toContainText('Rows', { timeout: 30_000 });
    const overview = page.getByRole('region', { name: 'Overview' });
    const cards = overview.locator('li.pin');
    await expect(cards).toHaveCount(3);
    await expect(cards.locator('.js-plotly-plot')).toHaveCount(3);
    await expect(cards.first()).toContainText(/over time/);
    await cards.nth(1).getByRole('button', { name: /^Pin/ }).click();
    await expect(cards.nth(1).getByRole('button', { name: /is pinned/ })).toHaveText('Pinned ✓');
    // The pinned card joins the dashboard list below the overview.
    await expect(page.locator('ul.pins').last().locator('li.pin')).toHaveCount(1);
  });

  test('an unrelated schema gets the same cards, without a currency it never declared', async ({ page }) => {
    await signUpFresh(page);
    await page.getByTestId('file-input').setInputFiles(path.join(TRANSFER, 'support_cases.csv'));
    const dialog = page.getByRole('dialog', { name: 'Upload CSV files' });
    await expect(dialog.getByText('✓ Uploaded')).toHaveCount(1, { timeout: 30_000 });
    await dialog.getByRole('button', { name: 'Done' }).click();
    await page.goto('/dashboard');
    await page.getByRole('button', { name: 'Generate overview' }).click();
    const overview = page.getByRole('region', { name: 'Overview' });
    await expect(overview.locator('li.pin')).toHaveCount(3, { timeout: 30_000 });
    await expect(overview).toContainText('Total resolution minutes');
    await expect(overview).toContainText(/by team/);
    await expect(overview).not.toContainText('£');
  });

  test('a dataset without dates says why there is no trend card', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['products.csv']);
    await page.goto('/dashboard');
    await page.getByRole('button', { name: 'Generate overview' }).click();
    await expect(page.getByRole('list', { name: 'Cards not shown' })).toContainText('Trend over time: No date column', { timeout: 30_000 });
  });

  test('a stale pinned card re-runs its stored calculation even with AI turned off', async ({ page, browser }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['orders.csv']);
    await page.goto('/dashboard');
    await page.getByRole('button', { name: 'Generate overview' }).click();
    const overview = page.getByRole('region', { name: 'Overview' });
    await expect(overview.locator('li.pin')).toHaveCount(3, { timeout: 30_000 });
    await overview.locator('li.pin').nth(1).getByRole('button', { name: /^Pin/ }).click();
    const pinned = page.locator('ul.pins').last().locator('li.pin');
    await expect(pinned).toHaveCount(1);
    const resultId = async () => ((await pinned.innerText()).match(/R-[0-9A-F]{6}/) ?? [''])[0];
    const before = await resultId();
    expect(before).not.toBe('');

    // The admin switches AI off; the user then changes the dataset's declared currency.
    const admin = await browser.newPage();
    await signIn(admin, 'admin@example.com');
    await admin.goto('/settings');
    const aiSwitch = admin.getByLabel(/AI analysis/i).first();
    await aiSwitch.uncheck();
    await admin.getByRole('button', { name: /Save/ }).click();
    await expect(aiSwitch).not.toBeChecked();
    try {
      await page.goto('/data');
      await Promise.all([
        page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/api/datasets/') && r.ok()),
        page.getByLabel('Currency of orders').selectOption('USD'),
      ]);
      await page.goto('/dashboard');
      await expect(pinned).toContainText('currency changed from none to USD'); // an upload declares none

      await pinned.getByRole('button', { name: 'Re-run with current data' }).click();
      await expect(pinned).not.toContainText('currency changed', { timeout: 15_000 });
      await expect.poll(resultId).not.toBe(before); // the card now shows a new stored result
    } finally {
      // Other specs share this backend: always switch AI back on.
      await aiSwitch.check();
      await admin.getByRole('button', { name: /Save/ }).click();
      await expect(aiSwitch).toBeChecked();
      await admin.close();
    }
  });
});
