import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { Q, answerCard, ask, sample, signUpFresh, uploadSamples } from './helpers';

test.describe('result → chart switch → export → pin', () => {
  test('switches the chart type through the API, exports CSV and HTML, and pins to the dashboard', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page);
    await ask(page, Q.revenueByRegion);
    const card = answerCard(page);
    await expect(card.getByText('✓ VALIDATED')).toBeVisible({ timeout: 30_000 });

    // chart type switch: PATCH /api/results/{id}/chart, then the chart re-renders from the response
    const group = card.getByRole('group', { name: 'Chart type' });
    await expect(group).toBeVisible();
    await expect(group.getByRole('button', { name: 'bar' })).toHaveAttribute('aria-pressed', 'true');
    const patched = page.waitForResponse((r) => /\/api\/results\/[^/]+\/chart$/.test(r.url()) && r.request().method() === 'PATCH');
    await group.getByRole('button', { name: 'pie' }).click();
    const res = await patched;
    expect(res.status()).toBe(200);
    expect(res.request().headers()['x-requested-with']).toBe('fetch');
    expect(JSON.parse(res.request().postData() ?? '{}').chart.type).toBe('pie');
    await expect(group.getByRole('button', { name: 'pie' })).toHaveAttribute('aria-pressed', 'true');
    // the chart has a name and a table alternative
    await expect(card.getByRole('img', { name: /table that follows/ })).toBeVisible();
    await expect(card.getByRole('table', { name: /Results by group|Results/ }).first()).toBeAttached();

    // the choice is stored on the result, so it survives a reload of the conversation
    await page.reload();
    const again = answerCard(page);
    await expect(again.getByRole('group', { name: 'Chart type' }).getByRole('button', { name: 'pie' })).toHaveAttribute('aria-pressed', 'true');

    // exports are real downloads built from the stored result
    const csvDownload = page.waitForEvent('download');
    await again.getByRole('button', { name: 'Download CSV' }).click();
    const csv = await csvDownload;
    expect(csv.suggestedFilename()).toMatch(/\.csv$/);
    const csvText = await readFile((await csv.path()) as string, 'utf8');
    expect(csvText.split('\n')[0]).toContain('region');
    expect(csvText).toMatch(/West|North|South|East|Central/);

    const htmlDownload = page.waitForEvent('download');
    await again.getByRole('button', { name: 'Export HTML report' }).click();
    const html = await htmlDownload;
    expect(html.suggestedFilename()).toMatch(/\.html$/);
    expect(await readFile((await html.path()) as string, 'utf8')).toContain('<html');

    // pin → the dashboard shows it, with source versions
    await again.getByRole('button', { name: 'Pin to dashboard' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Pinned to your dashboard.' })).toBeVisible();
    await expect(again.getByRole('button', { name: 'Pinned ✓ — view' })).toBeVisible();
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /Dashboard/ }).click();
    const pin = page.locator('li.pin').first();
    await expect(pin).toBeVisible();
    await expect(pin).toContainText('orders v1');
    await expect(pin).toContainText('R-');
  });

  test('a replaced dataset marks the dashboard card out of date', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['orders.csv', 'customers.csv']);
    await ask(page, Q.revenueByRegion);
    await expect(answerCard(page).getByText('✓ VALIDATED')).toBeVisible({ timeout: 30_000 });
    await answerCard(page).getByRole('button', { name: 'Pin to dashboard' }).click();
    await expect(answerCard(page).getByRole('button', { name: 'Pinned ✓ — view' })).toBeVisible();

    // replace orders.csv with a different file (version 2)
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /^Data( [0-9]+)?$/ }).click();
    await page.getByRole('tab', { name: /orders/ }).click();
    await page.getByRole('button', { name: 'Replace file…' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Choose replacement file' }).click();
    await page.getByTestId('replace-input').setInputFiles(sample('orders.csv'));
    await expect(page.getByRole('status').filter({ hasText: /replaced with orders\.csv/ })).toBeVisible({ timeout: 30_000 });

    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /Dashboard/ }).click();
    const pin = page.locator('li.pin').first();
    await expect(pin.getByRole('status')).toContainText('Out of date.');
    await expect(pin.getByRole('button', { name: 'Re-run with current data' })).toBeVisible();
  });
});
