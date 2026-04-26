import { test, expect } from '@playwright/test';
import { getApi, seedSessionFromFixture } from '../helpers/api';
import { waitForPollConnected, gotoSession, waitForIdle } from '../helpers/wait';

test.describe('Mock session', () => {
  test('T-021 session-start-mock shows Claude Code Launched', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'single_turn');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await expect(page.getByText('Claude Code Launched')).toBeVisible({ timeout: 10_000 });
  });

  test('T-022 message-flow renders assistant response', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'single_turn');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await page.getByTestId('message-textarea').fill('Hello, how are you?');
    await page.getByTestId('send-button').click();
    await waitForIdle(page);
    await expect(page.getByTestId('assistant-message').first()).toContainText('doing well');
  });
});
