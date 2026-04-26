import { test, expect } from '@playwright/test';
import { waitForPollConnected } from '../helpers/wait';

test.describe('App load', () => {
  test('T-001 app-loads', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Claude/i);
    await expect(page.getByTestId('app-root')).toBeVisible();
  });

  test('T-002 poll-connects', async ({ page }) => {
    await page.goto('/');
    await waitForPollConnected(page);
  });
});
