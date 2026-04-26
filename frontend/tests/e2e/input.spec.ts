import { test, expect } from '@playwright/test';
import { getApi, seedSessionFromFixture } from '../helpers/api';
import { gotoSession, waitForPollConnected } from '../helpers/wait';

test.describe('Input area', () => {
  test('T-080 input-send-message appears in feed', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'single_turn');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await page.getByTestId('message-textarea').fill('Hello from T-080');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('user-message').first()).toContainText('Hello from T-080');
  });
});
