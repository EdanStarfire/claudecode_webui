import { test, expect } from '@playwright/test';
import { getApi, seedSessionFromFixture } from '../helpers/api';
import { gotoSession, waitForIdle, waitForPollConnected } from '../helpers/wait';

test.describe('Message rendering', () => {
  test('T-030 user-message-renders', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'single_turn');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await page.getByTestId('message-textarea').fill('Hello, how are you?');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('user-message').first()).toContainText('Hello, how are you?');
  });

  test('T-032 assistant-message-renders as markdown', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'single_turn');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await page.getByTestId('message-textarea').fill('Hello');
    await page.getByTestId('send-button').click();
    await waitForIdle(page);
    const assistant = page.getByTestId('assistant-message').first();
    await expect(assistant).toBeVisible();
    await expect(assistant.locator('.msg-text')).toBeVisible();
  });

  test('T-033 tool-call-renders timeline node', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'tool_use');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await page.getByTestId('message-textarea').fill('Read the file at /tmp/test.txt');
    await page.getByTestId('send-button').click();
    await waitForIdle(page);
    await expect(page.getByTestId('activity-timeline')).toBeVisible();
    await expect(page.getByTestId('timeline-node').first()).toBeVisible();
  });
});
