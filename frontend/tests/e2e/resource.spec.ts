import { test, expect } from '@playwright/test';
import { getApi, seedSessionFromFixture } from '../helpers/api';
import { seedResourceForSession } from '../helpers/seed';
import { gotoSession, waitForPollConnected } from '../helpers/wait';

test.describe('Resource gallery', () => {
  test('T-073 resource-open-fullview', async ({ page }) => {
    test.skip(true, 'T-073: deferred — resource gallery is inside a collapsed right sidebar panel; opening and switching to the Resources tab requires additional test setup beyond MVP scope');

    const api = await getApi();
    const { sessionId } = await seedSessionFromFixture(api, 'single_turn');
    await seedResourceForSession(sessionId);
    await page.goto('/');
    await waitForPollConnected(page);
    await gotoSession(page, sessionId);
    const item = page.getByTestId('resource-gallery').getByTestId('resource-item').first();
    await expect(item).toBeVisible({ timeout: 15_000 });
    await item.click();
    await expect(page.getByTestId('resource-fullview')).toBeVisible({ timeout: 5_000 });
  });
});
