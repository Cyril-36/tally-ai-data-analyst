import { expect, test } from '@playwright/test';
import { sample, signIn, signUpFresh, uploadSamples } from './helpers';

/**
 * These cover the endpoints listed under "Backend gaps" in docs/FRONTEND_MAP.md (rename, rows
 * pagination, sample loading, quality examples, sample/retention settings, provider test). They
 * need the backend branch that implements them; until it is merged they fail with the API's own
 * error shown in the UI, which is the intended behaviour.
 */
test.describe('backend gap endpoints', () => {
  test('rename a dataset', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['products.csv']);
    await page.goto('/data');
    await page.getByRole('button', { name: 'Rename' }).click();
    const input = page.getByLabel('Dataset name');
    await input.fill('catalogue');
    await input.press('Enter');
    await expect(page.getByRole('heading', { name: 'catalogue', level: 3 })).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Workspace' }).getByText('catalogue', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'catalogue', level: 3 })).toBeVisible();
  });

  test('page through the dataset preview', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['orders.csv']);
    await page.goto('/data');
    await expect(page.getByText(/Rows 1–25 of 4,971/)).toBeVisible();
    await page.getByRole('button', { name: 'Next →' }).click();
    await expect(page.getByText(/Rows 26–50 of 4,971/)).toBeVisible();
    await expect(page.getByRole('table', { name: 'Data preview' })).toContainText('O000026');
    await page.getByRole('button', { name: '← Previous' }).click();
    await expect(page.getByText(/Rows 1–25 of 4,971/)).toBeVisible();
  });

  test('load the sample data without duplicating it', async ({ page }) => {
    await signUpFresh(page);
    await page.getByRole('button', { name: /Try a retail sample/ }).click();
    const side = page.getByRole('complementary', { name: 'Workspace' });
    for (const f of ['orders.csv', 'customers.csv', 'products.csv', 'payments.csv']) {
      await expect(side.getByText(new RegExp(`^${f.replace('.', '\\.')} · `))).toBeVisible({ timeout: 30_000 });
    }
    await page.goto('/data');
    await page.getByRole('button', { name: 'Load sample data' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'already loaded' })).toBeVisible();
    await expect(side.getByText(/^orders\.csv · /)).toHaveCount(1);
  });

  test('shows example affected rows for a quality finding', async ({ page }) => {
    await signUpFresh(page);
    await page.getByRole('button', { name: 'Upload' }).first().click();
    await page.getByTestId('file-input').setInputFiles(sample('messy_customers.csv'));
    await expect(page.getByRole('dialog').getByText('✓ Uploaded')).toBeVisible({ timeout: 20_000 });
    await page.keyboard.press('Escape');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /Data quality/ }).click();
    const first = page.locator('.q-find__btn').first();
    await first.click();
    await expect(first).toHaveAttribute('aria-expanded', 'true');
    const panel = page.locator('.q-find__ex').first();
    await expect(panel.getByRole('table')).toBeVisible();
    await expect(panel.getByRole('alert')).toHaveCount(0);
    expect(await panel.getByRole('row').count()).toBeGreaterThan(1);
    expect(await panel.getByRole('row').count()).toBeLessThanOrEqual(6); // header + up to 5 rows
  });

  test('admin settings: retention, sample toggle, provider test, and the AI switch persist', async ({ page }) => {
    await signIn(page, 'admin@example.com');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /Admin settings/ }).click();
    try {
      const retention = page.getByLabel('Conversation retention');
      await expect(retention).toBeVisible();
      await retention.selectOption({ label: '30 days' });
      await page.getByLabel('Offer sample data').uncheck();
      await page.getByRole('switch', { name: /AI analysis/ }).uncheck();
      await page.getByRole('button', { name: 'Save settings' }).click();
      await expect(page.getByRole('status').filter({ hasText: 'Settings saved.' })).toBeVisible();

      await page.reload();
      await expect(page.getByLabel('Conversation retention')).toHaveValue('30');
      await expect(page.getByLabel('Offer sample data')).not.toBeChecked();
      await expect(page.getByRole('switch', { name: /AI analysis/ })).not.toBeChecked();
    } finally {
      // never leave the shared backend with AI switched off for the specs that follow
      await page.request.put('/api/admin/settings', {
        headers: { 'X-Requested-With': 'fetch' },
        data: { max_upload_mb: 50, llm_enabled: true, sample_data_enabled: true, retention_days: 90 },
      });
    }
    await page.reload();
    await expect(page.getByRole('switch', { name: /AI analysis/ })).toBeChecked();
    await page.getByRole('button', { name: 'Test connection' }).click();
    await expect(page.getByRole('status').filter({ hasText: /responded in \d+ ms|Provider test failed|did not respond/ })).toBeVisible();
  });
});
