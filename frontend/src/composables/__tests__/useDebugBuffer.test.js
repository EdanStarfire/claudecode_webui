import { describe, it, expect, beforeEach, vi } from 'vitest'

const apiPostMock = vi.fn()
vi.mock('@/utils/api', () => ({
  api: { post: (...args) => apiPostMock(...args) }
}))

vi.mock('@/stores/session', () => ({
  useSessionStore: () => ({ currentSessionId: 's1' })
}))

beforeEach(() => {
  vi.resetModules()
  apiPostMock.mockReset()
  apiPostMock.mockResolvedValue({})
  vi.useRealTimers()
})

describe('useDebugBuffer', () => {
  it('pushDebugEvent past the 1000 cap evicts the oldest entries', async () => {
    const { pushDebugEvent, flushDebugBuffer } = await import('@/composables/useDebugBuffer')

    for (let i = 0; i < 1005; i++) {
      pushDebugEvent('test', 'tag', { i })
    }
    await flushDebugBuffer('test')

    const events = apiPostMock.mock.calls[0][1].events
    expect(events.length).toBe(1000)
    expect(events[0].data.i).toBe(5)
    expect(events[events.length - 1].data.i).toBe(1004)
  })

  it('truncates an oversized payload instead of storing it raw', async () => {
    const { pushDebugEvent, flushDebugBuffer } = await import('@/composables/useDebugBuffer')

    const bigString = 'x'.repeat(3000)
    pushDebugEvent('test', 'tag', { bigString })
    await flushDebugBuffer('test')

    const [event] = apiPostMock.mock.calls[0][1].events
    expect(event.data.truncated).toBe(true)
    expect(event.data.preview.length).toBe(2000)
  })

  it('does not truncate a payload within the size budget', async () => {
    const { pushDebugEvent, flushDebugBuffer } = await import('@/composables/useDebugBuffer')

    pushDebugEvent('test', 'tag', { small: 'value' })
    await flushDebugBuffer('test')

    const [event] = apiPostMock.mock.calls[0][1].events
    expect(event.data).toEqual({ small: 'value' })
  })

  it('debounces rapid repeated flushDebugBuffer calls into a single network call', async () => {
    const { pushDebugEvent, flushDebugBuffer } = await import('@/composables/useDebugBuffer')

    pushDebugEvent('test', 'tag', {})
    const [first, second, third] = await Promise.all([
      flushDebugBuffer('first'),
      flushDebugBuffer('second'),
      flushDebugBuffer('third'),
    ])

    expect(apiPostMock).toHaveBeenCalledTimes(1)
    // Only the flush that actually reaches the network should report true — callers
    // (e.g. the right-click confirmation UI) rely on this to avoid a false "sent" state.
    expect(first).toBe(true)
    expect(second).toBe(false)
    expect(third).toBe(false)
  })

  it('does not drain the buffer on a successful submission', async () => {
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    const { pushDebugEvent, flushDebugBuffer } = await import('@/composables/useDebugBuffer')

    pushDebugEvent('test', 'tag', { n: 1 })
    await flushDebugBuffer('first')
    const firstCount = apiPostMock.mock.calls[0][1].events.length

    // Move past the debounce window so a second flush is allowed through.
    nowSpy.mockReturnValue(1_000_000 + 3100)
    await flushDebugBuffer('second')
    const secondCount = apiPostMock.mock.calls[1][1].events.length

    expect(secondCount).toBe(firstCount)
    nowSpy.mockRestore()
  })

  it('does not drain the buffer when the submission fails', async () => {
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    apiPostMock.mockRejectedValueOnce(new Error('network down'))
    const { pushDebugEvent, flushDebugBuffer } = await import('@/composables/useDebugBuffer')
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    pushDebugEvent('test', 'tag', { n: 1 })
    await flushDebugBuffer('first')
    expect(warnSpy).toHaveBeenCalled()

    nowSpy.mockReturnValue(1_000_000 + 3100)
    apiPostMock.mockResolvedValueOnce({})
    await flushDebugBuffer('second')
    const events = apiPostMock.mock.calls[1][1].events
    expect(events.length).toBe(1)

    warnSpy.mockRestore()
    nowSpy.mockRestore()
  })

  it('includes session id and user agent in the payload automatically', async () => {
    const { pushDebugEvent, flushDebugBuffer } = await import('@/composables/useDebugBuffer')

    pushDebugEvent('test', 'tag', {})
    await flushDebugBuffer('manual')

    const payload = apiPostMock.mock.calls[0][1]
    expect(payload.session_id).toBe('s1')
    expect(payload.browser.user_agent).toBeDefined()
  })
})
