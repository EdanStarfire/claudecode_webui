import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import AuthPrompt from '@/components/common/AuthPrompt.vue'

const apiGetMock = vi.hoisted(() => vi.fn())
const setAuthTokenMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() },
  apiGet: apiGetMock,
  setAuthToken: setAuthTokenMock,
  getAuthToken: vi.fn().mockReturnValue(null),
  clearAuthToken: vi.fn()
}))

describe('AuthPrompt', () => {
  it('submitting token calls setAuthToken and verifies via apiGet', async () => {
    const user = userEvent.setup()
    apiGetMock.mockResolvedValue({ authenticated: true })

    const { emitted } = renderWithStores(AuthPrompt)

    const input = screen.getByPlaceholderText(/paste token/i)
    await user.type(input, 'my-secret-token')

    const btn = screen.getByRole('button', { name: /authenticate/i })
    await user.click(btn)

    expect(setAuthTokenMock).toHaveBeenCalledWith('my-secret-token')
    expect(apiGetMock).toHaveBeenCalledWith('/api/auth/check')
    expect(emitted().authenticated).toBeTruthy()
  })
})
