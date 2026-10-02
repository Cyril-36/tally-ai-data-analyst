import { expect, test } from '@playwright/test';
import { DEMO_PASSWORD, sample, signIn, signUpFresh } from './helpers';

test.describe('sign-in', () => {
  test('a wrong password shows the API message and stays on the sign-in screen', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
    await page.getByLabel('Email').fill('analyst1@example.com');
    await page.getByLabel('Password').fill('definitely-not-the-password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Invalid email or password.');
    await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
  });

  test('analyst signs in, sees no admin item, and can sign out', async ({ page }) => {
    await signIn(page, 'analyst1@example.com');
    const nav = page.getByRole('navigation', { name: 'Main' });
    await expect(nav.getByRole('link', { name: 'Dashboard' })).toBeVisible();
    await expect(nav.getByRole('link', { name: /Admin settings/ })).toHaveCount(0);
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/ask$/); // admin route bounces a non-admin
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
    // the session cookie is gone: a reload stays signed out
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  });

  test('admin sees Admin settings including the AI switch', async ({ page }) => {
    await signIn(page, 'admin@example.com');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /Admin settings/ }).click();
    await expect(page.getByRole('heading', { name: 'Admin settings', level: 2 })).toBeVisible();
    await expect(page.getByRole('switch', { name: /AI analysis/ })).toBeVisible();
  });

  test('creates an account and lands in an empty workspace', async ({ page }) => {
    await signUpFresh(page);
    await expect(page.getByRole('heading', { name: /Start with a CSV file/ })).toBeVisible();
  });

  test('keeps the sign-in tabs keyboard operable', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('tab', { name: 'Sign in' }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: 'Create account' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('heading', { name: 'Create your workspace' })).toBeVisible();
  });

  test('a lost session turns into a sign-in prompt that keeps the typed question', async ({ page, context }) => {
    const email = await signUpFresh(page);
    await page.getByTestId('file-input').setInputFiles([sample('products.csv')]);
    await page.getByRole('dialog', { name: 'Upload CSV files' }).getByRole('button', { name: 'Done' }).click();
    await context.clearCookies(); // server no longer recognises us
    const box = page.getByLabel('Ask a question about your data');
    await box.fill('Show revenue for each region.');
    await box.press('Enter');
    const prompt = page.getByRole('dialog', { name: 'Sign in to continue' });
    await expect(prompt).toBeVisible();
    await expect(prompt.getByText(email)).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Main' })).toBeHidden();
    await prompt.getByLabel('Password').fill(DEMO_PASSWORD);
    await prompt.getByRole('button', { name: 'Sign in' }).click();
    await expect(prompt).toBeHidden();
    await expect(page.getByLabel('Ask a question about your data')).toHaveValue('Show revenue for each region.');
  });
});

test.describe('small screens', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('the sidebar is a drawer: opens from the menu button, closes on Escape, returns focus', async ({ page }) => {
    // On narrow screens the sidebar starts closed, so sign in by hand instead of waiting for the nav.
    await page.goto('/');
    await page.getByLabel('Email').fill('analyst2@example.com');
    await page.getByLabel('Password').fill(DEMO_PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    const toggle = page.getByRole('button', { name: 'Open menu' });
    await expect(toggle).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
    await toggle.click();
    await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
    await expect(toggle).toBeFocused();
    // no horizontal scroll at 390px
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
