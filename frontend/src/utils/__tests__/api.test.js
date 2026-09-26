import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { apiPost } from '../api.js'

async function gunzip(bytes) {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes)
      controller.close()
    }
  }).pipeThrough(new DecompressionStream('gzip'))
  const buffer = await new Response(stream).arrayBuffer()
  return new TextDecoder().decode(buffer)
}

function mockFetchOk(body = {}) {
  return vi.fn().mockResolvedValue({
    ok: true,
    headers: { get: () => 'application/json' },
    json: async () => body
  })
}

describe('apiPost request body compression (issue #2029)', () => {
  const originalCompressionStream = globalThis.CompressionStream

  beforeEach(() => {
    globalThis.CompressionStream = originalCompressionStream
  })

  afterEach(() => {
    globalThis.CompressionStream = originalCompressionStream
    vi.unstubAllGlobals()
  })

  it('sends small bodies uncompressed with no Content-Encoding header', async () => {
    const fetchMock = mockFetchOk()
    vi.stubGlobal('fetch', fetchMock)

    await apiPost('/api/sessions/123/messages', { message: 'hi' })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.body).toBe(JSON.stringify({ message: 'hi' }))
    expect(init.headers['Content-Encoding']).toBeUndefined()
  })

  it('gzip-compresses bodies above the threshold when CompressionStream is available', async () => {
    const fetchMock = mockFetchOk()
    vi.stubGlobal('fetch', fetchMock)

    const largePayload = { message: 'x'.repeat(9 * 1024) }
    await apiPost('/api/sessions/123/messages', largePayload)

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['Content-Encoding']).toBe('gzip')
    expect(init.body).toBeInstanceOf(Uint8Array)

    const decoded = await gunzip(init.body)
    expect(decoded).toBe(JSON.stringify(largePayload))
  })

  it('falls back to uncompressed when CompressionStream is unavailable', async () => {
    const fetchMock = mockFetchOk()
    vi.stubGlobal('fetch', fetchMock)
    // Simulate a browser without CompressionStream support (e.g. older Safari).
    globalThis.CompressionStream = undefined

    const largePayload = { message: 'x'.repeat(9 * 1024) }
    await apiPost('/api/sessions/123/messages', largePayload)

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['Content-Encoding']).toBeUndefined()
    expect(init.body).toBe(JSON.stringify(largePayload))
  })
})
