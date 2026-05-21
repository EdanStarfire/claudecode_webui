import { describe, it, expect } from 'vitest'
import { resourceTokenPlugin } from '../resourceToken.js'

function makeState(nodes) {
  return { tree: { nodes, frontmatter: {}, meta: {} } }
}

function runPlugin(nodes, token) {
  const plugin = resourceTokenPlugin({ getToken: () => token })
  const state = makeState(nodes)
  plugin.post(state)
  return state.tree.nodes
}

describe('resourceTokenPlugin', () => {
  it('appends token to resource image src', () => {
    const nodes = [['img', { src: '/api/sessions/abc/resources/def' }]]
    const result = runPlugin(nodes, 'mytoken')
    expect(result[0][1].src).toBe('/api/sessions/abc/resources/def?token=mytoken')
  })

  it('appends token to resource link href', () => {
    const nodes = [['a', { href: '/api/sessions/abc/resources/def' }, 'text']]
    const result = runPlugin(nodes, 'mytoken')
    expect(result[0][1].href).toBe('/api/sessions/abc/resources/def?token=mytoken')
  })

  it('URI-encodes the token', () => {
    const nodes = [['img', { src: '/api/sessions/abc/resources/def' }]]
    const result = runPlugin(nodes, 'tok en+with/special=chars')
    expect(result[0][1].src).toBe('/api/sessions/abc/resources/def?token=tok%20en%2Bwith%2Fspecial%3Dchars')
  })

  it('does not modify non-resource URLs', () => {
    const nodes = [
      ['a', { href: 'https://example.com' }, 'text'],
      ['img', { src: 'https://example.com/img.png' }],
    ]
    const result = runPlugin(nodes, 'mytoken')
    expect(result[0][1].href).toBe('https://example.com')
    expect(result[1][1].src).toBe('https://example.com/img.png')
  })

  it('does not modify /api/ URLs that are not resource paths', () => {
    const nodes = [['a', { href: '/api/sessions/abc/messages' }, 'text']]
    const result = runPlugin(nodes, 'mytoken')
    expect(result[0][1].href).toBe('/api/sessions/abc/messages')
  })

  it('does not modify resource URLs that already have a query string', () => {
    const nodes = [['img', { src: '/api/sessions/abc/resources/def?existing=1' }]]
    const result = runPlugin(nodes, 'mytoken')
    expect(result[0][1].src).toBe('/api/sessions/abc/resources/def?existing=1')
  })

  it('no-ops when token is null', () => {
    const nodes = [['img', { src: '/api/sessions/abc/resources/def' }]]
    const plugin = resourceTokenPlugin({ getToken: () => null })
    const state = makeState(nodes)
    plugin.post(state)
    expect(state.tree.nodes[0][1].src).toBe('/api/sessions/abc/resources/def')
  })

  it('no-ops when getToken is not provided', () => {
    const nodes = [['img', { src: '/api/sessions/abc/resources/def' }]]
    const plugin = resourceTokenPlugin({})
    const state = makeState(nodes)
    plugin.post(state)
    expect(state.tree.nodes[0][1].src).toBe('/api/sessions/abc/resources/def')
  })
})
