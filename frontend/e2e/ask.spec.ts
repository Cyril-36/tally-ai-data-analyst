import { expect, test } from '@playwright/test';
import { Q, answerCard, ask, signUpFresh, uploadSamples } from './helpers';

test.describe('ask', () => {
  test('streams progress, then renders the validated answer from the stored result', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page);

    const streamed = page.waitForResponse((r) => r.url().includes('/ask') && r.request().method() === 'POST');
    await ask(page, Q.revenueByRegion);

    // Progress steps appear while the answer does not.
    await expect(page.getByText('Working · the answer appears once it’s checked')).toBeVisible();
    await expect(page.locator('article.answer')).toHaveCount(0);

    const response = await streamed;
    expect(response.headers()['content-type']).toContain('text/event-stream');
    const sent = response.request().headers();
    expect(sent['x-requested-with']).toBe('fetch');
    expect(sent['accept']).toBe('text/event-stream');
    expect(JSON.parse(response.request().postData() ?? '{}').request_key).toMatch(/^[0-9a-f-]{20,}$/);

    const card = answerCard(page);
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card.getByText('✓ CALCULATION CHECKED')).toBeVisible();
    await expect(card.getByRole('heading', { name: Q.revenueByRegion })).toBeVisible();
    // sentences are the backend's own; the table carries the real figures
    await expect(card.locator('.answer__conclusion')).not.toBeEmpty();
    const table = card.getByRole('table', { name: Q.revenueByRegion });
    await expect(table.getByRole('row')).not.toHaveCount(1);
    await expect(table).toContainText(/West|North|South|East|Central/);

    // method details: SQL and bound parameters, source versions, caveats
    await card.getByRole('button', { name: /Analysis details/ }).click();
    await expect(card.getByLabel('SQL that ran')).toContainText('SELECT');
    await expect(card.getByText('orders (v1)')).toBeVisible();
    await expect(card.getByText('Assumptions & limitations')).toBeVisible();
    await expect(card.getByText('Parameters')).toBeVisible();
    // no hard-coded USD anywhere in the answer
    expect(await card.innerText()).not.toContain('$');

    // the composer is ready for a follow-up and the conversation is listed
    await expect(page.getByRole('complementary', { name: 'Workspace' }).getByText(Q.revenueByRegion.slice(0, 20))).toBeVisible();
  });

  test('an unsupported question gets its own notice, never an answer', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page);
    await ask(page, Q.unsupported);
    const notice = page.getByRole('group', { name: /not supported|Tally declined|not enough data/i });
    await expect(notice).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('article.answer')).toHaveCount(0);
    await expect(notice.getByRole('button', { name: 'Edit question' })).toBeVisible();
    await notice.getByRole('button', { name: 'Edit question' }).click();
    await expect(page.getByLabel('Ask a question about your data')).toHaveValue(Q.unsupported);
  });

  test('a question with no dataset selected is held back and the question kept', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page, ['products.csv']);
    await page.getByRole('checkbox', { name: 'Use products.csv for analysis' }).uncheck();
    await ask(page, Q.revenueByRegion);
    await expect(page.getByRole('alert')).toContainText('Select at least one dataset');
    await expect(page.getByLabel('Ask a question about your data')).toHaveValue(Q.revenueByRegion);
  });
});

test.describe('clarification', () => {
  test('an ambiguous question asks for clarification; choosing an option asks the follow-up', async ({ page }) => {
    await signUpFresh(page);
    await uploadSamples(page);
    await ask(page, Q.underperforming);

    const clarify = page.locator('article.clarify');
    await expect(clarify).toBeVisible({ timeout: 30_000 });
    await expect(clarify.getByText('Needs clarification')).toBeVisible();
    await expect(page.locator('article.answer')).toHaveCount(0);
    const options = clarify.getByRole('group', { name: 'Options' }).getByRole('button');
    await expect(options.first()).toBeVisible();
    const label = (await options.first().innerText()).trim();

    const followUp = page.waitForRequest((r) => r.url().includes('/ask') && r.method() === 'POST');
    await options.first().click();
    const body = JSON.parse((await followUp).postData() ?? '{}');
    expect(body.question).toBe(label);
    await expect(clarify.getByText(`Answered: “${label}”`)).toBeVisible();
    // the option is shown as the user's reply and something comes back (answer or notice)
    await expect(page.locator('.bubble', { hasText: label })).toBeVisible();
    await expect(page.locator('article.answer, article.notice').first()).toBeVisible({ timeout: 30_000 });
  });
});
