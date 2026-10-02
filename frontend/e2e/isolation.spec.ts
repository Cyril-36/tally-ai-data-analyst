import { expect, test } from '@playwright/test';
import { Q, answerCard, ask, signIn, uploadSamples } from './helpers';

test('cross-user refusal: analyst2 cannot open analyst1’s conversation, result, export or dataset', async ({ browser }) => {
  // analyst1 builds a conversation with a stored result
  const owner = await (await browser.newContext()).newPage();
  await signIn(owner, 'analyst1@example.com');
  await uploadSamples(owner, ['orders.csv', 'customers.csv']);
  await ask(owner, Q.revenueByRegion);
  await expect(answerCard(owner).getByText('✓ CALCULATION CHECKED')).toBeVisible({ timeout: 30_000 });
  const conversationId = new URL(owner.url()).pathname.split('/').pop() as string;
  const { datasetId, resultId } = await owner.evaluate(async (cid) => {
    const datasets = (await (await fetch('/api/datasets')).json()) as Array<{ id: string }>;
    const results = (await (await fetch(`/api/conversations/${cid}/results`)).json()) as Array<{ result: { id: string } }>;
    return { datasetId: datasets[0].id, resultId: results[0].result.id };
  }, conversationId);
  expect(conversationId).toMatch(/^[0-9a-f]{32}$/);

  // analyst2 is signed in separately and gets the same answer as for a missing resource
  const other = await (await browser.newContext()).newPage();
  await signIn(other, 'analyst2@example.com');

  await other.goto(`/ask/${conversationId}`);
  await expect(other.getByRole('group', { name: 'Unavailable' })).toContainText(/not found or you don't have access/i);
  await expect(other.locator('article.answer')).toHaveCount(0);
  await expect(other.locator('.bubble')).toHaveCount(0);

  await other.goto(`/data/${datasetId}`);
  await expect(other.getByRole('group', { name: 'Unavailable' })).toContainText(/not found or you don't have access/i);
  await expect(other.getByText('O000001')).toHaveCount(0);

  const statuses = await other.evaluate(
    async ({ resultId, datasetId, conversationId }) => {
      const get = async (path: string) => (await fetch(path)).status;
      return {
        result: await get(`/api/results/${resultId}`),
        csv: await get(`/api/results/${resultId}/export?format=csv`),
        html: await get(`/api/results/${resultId}/export?format=html`),
        chart: await get(`/api/results/${resultId}/chart`),
        dataset: await get(`/api/datasets/${datasetId}`),
        rows: await get(`/api/datasets/${datasetId}/rows?offset=0&limit=5`),
        conversation: await get(`/api/conversations/${conversationId}/results`),
        mine: await (await fetch('/api/datasets')).json(),
      };
    },
    { resultId, datasetId, conversationId },
  );
  expect([statuses.result, statuses.csv, statuses.html, statuses.chart, statuses.dataset, statuses.conversation]).toEqual([404, 404, 404, 404, 404, 404]);
  expect(statuses.rows).toBe(404);
  expect(statuses.mine).toEqual([]);
});
