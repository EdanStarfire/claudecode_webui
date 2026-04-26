import { test, expect } from '@playwright/test';
import { getApi, seedSessionFromFixture } from '../helpers/api';
import { gotoSession, waitForPollConnected, waitForIdle } from '../helpers/wait';

test.describe('Permission flow', () => {
  /**
   * T-050: Verify the Edit tool_use appears in the ActivityTimeline when
   * the permission_flow fixture is replayed. The mock SDK auto-advances
   * past permission actions, so we test that the tool node is rendered
   * rather than the transient permission modal UI.
   */
  test('T-050 tool-appears-in-timeline', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'permission_flow');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await page.getByTestId('message-textarea').fill('Edit the file at /tmp/test.txt');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('activity-timeline')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('timeline-node').first()).toBeVisible();
  });

  /**
   * T-051: Verify the session completes after the permission flow.
   * The mock SDK auto-allows the permission and replays the post-permission
   * segment (tool result + final assistant message + ResultMessage).
   */
  test('T-051 session-completes-after-permission', async ({ page }) => {
    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'permission_flow');
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    await page.getByTestId('message-textarea').fill('Edit the file at /tmp/test.txt');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('assistant-message').last()).toContainText(/Done|edited/i, {
      timeout: 15_000,
    });
    await waitForIdle(page);
  });
});
