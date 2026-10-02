import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { sample, signUpFresh, uploadSamples } from './helpers';

test.describe('upload', () => {
  test('uploads several CSVs from the dialog, lists them, and previews the real rows', async ({ page }) => {
    await signUpFresh(page);
    await page.getByRole('button', { name: 'Upload' }).first().click();
    const dialog = page.getByRole('dialog', { name: 'Upload CSV files' });
    await expect(dialog).toBeVisible();
    await page.getByTestId('file-input').setInputFiles([sample('orders.csv'), sample('customers.csv')]);
    await expect(dialog.getByText('✓ Uploaded')).toHaveCount(2, { timeout: 30_000 });
    await expect(dialog.getByText(/4,971 rows/)).toBeVisible();

    await dialog.getByRole('button', { name: 'Inspect →' }).first().click();
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(/\/data\/[0-9a-f]+/);

    const side = page.getByRole('complementary', { name: 'Workspace' });
    await expect(side.getByText('orders.csv · 4,971 rows')).toBeVisible();
    await expect(side.getByText('customers.csv · 400 rows')).toBeVisible();
    await expect(side.getByText('2 of 2 selected for analysis')).toBeVisible();

    // The preview shows rows straight from the stored file.
    await page.getByRole('tab', { name: /orders/ }).click();
    await expect(page.getByRole('table', { name: 'Data preview' })).toContainText('O000001');
    await page.getByRole('tab', { name: /^Columns/ }).click();
    await expect(page.getByRole('table', { name: 'Columns' })).toContainText('discount_amount');
  });

  test('a file the server rejects is reported without removing one that succeeded', async ({ page }) => {
    await signUpFresh(page);
    await page.getByRole('button', { name: 'Upload' }).first().click();
    const dialog = page.getByRole('dialog', { name: 'Upload CSV files' });
    await page.getByTestId('file-input').setInputFiles([
      { name: 'products.csv', mimeType: 'text/csv', buffer: readFileSync(sample('products.csv')) },
      { name: 'broken.csv', mimeType: 'text/csv', buffer: Buffer.from('') },
    ]);
    await expect(dialog.getByText('✓ Uploaded')).toHaveCount(1, { timeout: 30_000 });
    await expect(dialog.getByText('✕ Rejected')).toHaveCount(1);
    await expect(dialog.getByRole('alert')).not.toBeEmpty();
    await dialog.getByRole('button', { name: 'Done' }).click();
    await expect(page.getByRole('complementary', { name: 'Workspace' }).getByText('products.csv · 30 rows')).toBeVisible();
  });

  test('accepts a dropped file on the onboarding drop zone', async ({ page }) => {
    await signUpFresh(page);
    const zone = page.getByRole('button', { name: /Upload CSV files: drop files here/ });
    const csv = 'product_id,product_name\nP1,Mouse\nP2,Keyboard\n';
    const data = await page.evaluateHandle((text) => {
      const dt = new DataTransfer();
      dt.items.add(new File([text], 'dropped.csv', { type: 'text/csv' }));
      return dt;
    }, csv);
    await zone.dispatchEvent('drop', { dataTransfer: data });
    const dialog = page.getByRole('dialog', { name: 'Upload CSV files' });
    await expect(dialog.getByText('✓ Uploaded')).toBeVisible({ timeout: 20_000 });
    await expect(dialog.getByText(/2 rows/)).toBeVisible();
  });

  test('the drop zone works from the keyboard', async ({ page }) => {
    await signUpFresh(page);
    const zone = page.getByRole('button', { name: /Upload CSV files: drop files here/ });
    await zone.focus();
    const chooser = page.waitForEvent('filechooser');
    await page.keyboard.press('Enter');
    await (await chooser).setFiles(sample('products.csv'));
    await expect(page.getByRole('dialog', { name: 'Upload CSV files' }).getByText('✓ Uploaded')).toBeVisible({ timeout: 20_000 });
  });

  test('dialog traps focus, closes on Escape and returns focus to its opener', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['products.csv']);
    const opener = page.getByRole('button', { name: 'Upload' }).first();
    await opener.focus();
    await opener.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Upload CSV files' });
    await expect(dialog).toBeVisible();
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press('Tab');
      const inside = await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'));
      expect(inside).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
});
