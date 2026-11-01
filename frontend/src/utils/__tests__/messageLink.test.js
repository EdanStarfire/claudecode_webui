import { describe, it, expect, vi } from 'vitest'
import { buildMessageLink, copyText } from '@/utils/messageLink'

const loc = (hash, extra = {}) => ({ origin: 'https://host:8000', pathname: '/', hash, ...extra })

describe('buildMessageLink (issue #2030 stage 3 PR 3F)', () => {
  it('puts msg inside the hash route of the live session, an archive and a deleted agent', () => {
    expect(buildMessageLink('m-1', loc('#/session/s1'))).toBe('https://host:8000/#/session/s1?msg=m-1')
    expect(buildMessageLink('m-1', loc('#/session/s1/archive/a1'))).toBe('https://host:8000/#/session/s1/archive/a1?msg=m-1')
    expect(buildMessageLink('m-1', loc('#/archive/agent/ag/a1'))).toBe('https://host:8000/#/archive/agent/ag/a1?msg=m-1')
  })

  it('replaces an existing query (including an earlier msg) and encodes the id', () => {
    expect(buildMessageLink('a b/c', loc('#/session/s1?msg=old&x=1'))).toBe('https://host:8000/#/session/s1?msg=a%20b%2Fc')
  })

  it('keeps a sub-path deployment and falls back to the root route', () => {
    expect(buildMessageLink('m', loc('#/session/s1', { pathname: '/ui/' }))).toBe('https://host:8000/ui/#/session/s1?msg=m')
    expect(buildMessageLink('m', loc(''))).toBe('https://host:8000/#/?msg=m')
  })
})

describe('copyText', () => {
  it('uses the async clipboard when present', async () => {
    const writeText = vi.fn().mockResolvedValue()
    expect(await copyText('x', { nav: { clipboard: { writeText } } })).toBe(true)
    expect(writeText).toHaveBeenCalledWith('x')
  })

  it('falls back to a hidden textarea + execCommand where the async clipboard is missing (plain http) or rejects', async () => {
    const exec = vi.fn(() => true)
    const doc = document
    doc.execCommand = exec
    expect(await copyText('y', { nav: {}, doc })).toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull() // removed again
    exec.mockClear()
    expect(await copyText('z', { nav: { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } }, doc })).toBe(true)
    expect(exec).toHaveBeenCalledTimes(1)
  })

  it('reports failure when nothing can copy', async () => {
    document.execCommand = vi.fn(() => false)
    expect(await copyText('q', { nav: {} })).toBe(false)
  })
})
