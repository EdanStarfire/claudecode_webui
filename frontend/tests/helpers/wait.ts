import { Page, expect } from '@playwright/test';

/** Wait until UI long-poll has connected (HeaderRow1 indicator shows .connected class). */
export async function waitForPollConnected(page: Page): Promise<void> {
  // HeaderRow1.header-indicator gets class "connected" when uiConnected = true
  const indicator = page.getByTestId('connection-indicator');
  await expect(indicator).toBeVisible({ timeout: 10_000 });
  await expect(indicator).toHaveClass(/connected/, { timeout: 10_000 });
}

/**
 * Wait until session is idle (mock SDK replay complete).
 * The Send button renders only when !isProcessing (vs Stop button when processing).
 * It may be disabled (no content) but its presence signals idle state.
 */
export async function waitForIdle(page: Page): Promise<void> {
  await expect(page.getByTestId('send-button')).toBeVisible({ timeout: 15_000 });
}

/** Wait until N user/assistant message bubbles are present. */
export async function waitForMessageCount(page: Page, count: number): Promise<void> {
  await expect(
    page.locator('[data-testid="user-message"], [data-testid="assistant-message"]'),
  ).toHaveCount(count, { timeout: 15_000 });
}

/** Navigate to a session's hash route and wait for its DOM. */
export async function gotoSession(page: Page, sessionId: string): Promise<void> {
  await page.goto(`/#/session/${sessionId}`);
  await expect(page.getByTestId('message-list')).toBeVisible({ timeout: 10_000 });
}
