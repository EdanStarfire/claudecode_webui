import { test, expect } from '@playwright/test';
import { waitForPollConnected } from '../helpers/wait';

test.use({ viewport: { width: 375, height: 812 } });

test.describe('Mobile layout', () => {
  test('T-100 mobile-layout-375 no overflow', async ({ page }) => {
    await page.goto('/');
    await waitForPollConnected(page);
    const overflow = await page.evaluate(() => {
      const el = document.documentElement;
      return el.scrollWidth > el.clientWidth;
    });
    expect(overflow).toBe(false);
    await expect(page.getByTestId('app-root')).toBeVisible();
  });
});
