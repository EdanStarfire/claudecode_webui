import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import RelayPermissionPrompt from '@/components/messages/tools/RelayPermissionPrompt.vue'
import PermissionPrompt from '@/components/messages/tools/PermissionPrompt.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const ASK = {
  kind: 'ask_send', tool: 'post', action: 'send', channel_id: 'C0ASK00001',
  channel_name: 'ask-ch', channel_label: '#ask-ch', policy: 'ask', text_preview: 'hello team'
}
const CHOOSE = { ...ASK, kind: 'choose_policy', tool: 'read_thread', action: 'read', policy: null, text_preview: '' }

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.post.mockResolvedValue({})
})

describe('RelayPermissionPrompt', () => {
  it('ask_send shows channel + preview and emits allow-once / promote / deny', async () => {
    const user = userEvent.setup()
    const { emitted } = renderWithStores(RelayPermissionPrompt, { props: { prompt: ASK } })
    expect(screen.getByTestId('relay-channel').textContent).toBe('#ask-ch')
    expect(screen.getByTestId('relay-preview').textContent).toBe('hello team')

    await user.click(screen.getByTestId('relay-allow-once'))
    await user.click(screen.getByTestId('relay-allow-auto'))
    await user.click(screen.getByTestId('relay-deny'))
    expect(emitted().decide.map(e => e[0])).toEqual([
      { decision: 'allow', relayChoice: null },
      { decision: 'allow', relayChoice: { policy: null, promote: true } },
      { decision: 'deny', relayChoice: null }
    ])
  })

  it('shows the resolved label as plain text (no markup rendering)', () => {
    renderWithStores(RelayPermissionPrompt, {
      props: { prompt: { ...ASK, channel_label: 'DM with <b>Alice</b>' } }
    })
    expect(screen.getByTestId('relay-channel').textContent).toBe('DM with <b>Alice</b>')
    expect(screen.getByTestId('relay-channel').querySelector('b')).toBeNull()
  })

  it('falls back to the channel id when no label was resolved', () => {
    renderWithStores(RelayPermissionPrompt, { props: { prompt: { ...ASK, channel_label: undefined } } })
    expect(screen.getByTestId('relay-channel').textContent).toBe('C0ASK00001')
  })

  it('choose_policy offers the four policies and emits the selected one', async () => {
    const user = userEvent.setup()
    const { emitted } = renderWithStores(RelayPermissionPrompt, { props: { prompt: CHOOSE } })
    for (const p of ['blocked', 'read_only', 'ask', 'auto']) {
      expect(screen.getByTestId(`relay-policy-${p}`)).toBeTruthy()
    }
    expect(screen.queryByTestId('relay-allow-once')).toBeNull()
    await user.click(screen.getByTestId('relay-policy-auto'))
    await user.click(screen.getByTestId('relay-save-policy'))
    expect(emitted().decide[0][0]).toEqual({
      decision: 'allow', relayChoice: { policy: 'auto', promote: false }
    })
  })

  it('selecting blocked relabels the save button', async () => {
    const user = userEvent.setup()
    renderWithStores(RelayPermissionPrompt, { props: { prompt: CHOOSE } })
    await user.click(screen.getByTestId('relay-policy-blocked'))
    expect(screen.getByTestId('relay-save-policy').textContent).toBe('Save and block')
  })
})

describe('PermissionPrompt relay variant', () => {
  it('sends relay_choice through the permission REST call', async () => {
    const user = userEvent.setup()
    const toolCall = {
      id: 'use-relay-1', name: 'mcp__relay_slack__post', input: { channel: 'C0ASK00001', text: 'hello team' },
      status: 'permission_required', backendStatus: 'awaiting_permission',
      permissionRequestId: 'req-9', suggestions: [], isExpanded: true, relayPrompt: ASK
    }
    const { pinia } = renderWithStores(PermissionPrompt, { props: { toolCall } })
    const { useSessionStore } = await import('@/stores/session')
    useSessionStore(pinia).currentSessionId = 'sess-1'

    expect(screen.getByTestId('relay-permission-prompt')).toBeTruthy()
    expect(screen.queryByTestId('permission-allow-button')).toBeNull()
    await user.click(screen.getByTestId('relay-allow-auto'))
    await new Promise(r => setTimeout(r, 0))
    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/sess-1/permission/req-9', {
      decision: 'allow', apply_suggestions: false, relay_choice: { policy: null, promote: true }
    })
  })
})
