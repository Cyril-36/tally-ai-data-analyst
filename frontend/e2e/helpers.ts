import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, type Page } from '@playwright/test';

export const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? 'e2e-demo-password-1';
export const SAMPLE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../data/sample');
export const sample = (name: string) => path.join(SAMPLE_DIR, name);

/**
 * Questions the backend's scripted fake provider (LLM_PROVIDER=fake) answers. They are the
 * golden questions in evals/cases.py; keep this list in step with docs/FRONTEND_MAP.md gap 7.
 */
export const Q = {
  revenueByRegion: 'Show revenue for each region.',
  monthlyRevenue: 'Show monthly revenue during calendar year 2026.',
  topCustomers: 'Who are the top five customers by revenue?',
  underperforming: 'Which products are underperforming?',
  unsupported: 'What is our employee attrition rate?',
  anomalies: 'Detect anomalies in monthly revenue by region.',
};

export async function signIn(page: Page, email: string): Promise<void> {
  await page.goto('/');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(DEMO_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).last().click();
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
}

let counter = 0;
export function freshEmail(): string {
  return `e2e-${Date.now().toString(36)}-${++counter}@example.com`;
}

/** Creates a brand-new analyst account (sign-up is open) so a spec starts with an empty workspace. */
export async function signUpFresh(page: Page): Promise<string> {
  const email = freshEmail();
  await page.goto('/');
  await page.getByRole('tab', { name: 'Create account' }).click();
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(DEMO_PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).last().click();
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
  return email;
}

/** Uploads sample CSVs through the hidden file input and closes the dialog. */
export async function uploadSamples(page: Page, files: string[] = ['orders.csv', 'customers.csv', 'products.csv']): Promise<void> {
  await page.getByTestId('file-input').setInputFiles(files.map(sample));
  const dialog = page.getByRole('dialog', { name: 'Upload CSV files' });
  await expect(dialog.getByText('✓ Uploaded')).toHaveCount(files.length, { timeout: 30_000 });
  await dialog.getByRole('button', { name: 'Done' }).click();
  await expect(dialog).toBeHidden();
}

export async function ask(page: Page, question: string): Promise<void> {
  const box = page.getByLabel('Ask a question about your data');
  await box.fill(question);
  await box.press('Enter');
}

/** The first answer card on the page (a validated answer). */
export const answerCard = (page: Page) => page.locator('article.answer').last();
