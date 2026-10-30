// Issue #2030 (stage 1b): in-memory stand-in for the 1a history window API
// (`GET .../messages?window=tail` / `?before=<cursor>&max_bytes=`), serving slices of a fixed
// record array. Cursors are opaque to the browser; here they are `b:<record index>`.
//
// The fake only models what the browser can observe: the slice boundaries, `start_cursor`,
// `has_more_before`, `history_id` and `latest_init`. Slice sizes are chosen by the test
// (`tailStart` / `pageSize`) so equivalence can be checked at every split point.

export function createFakeHistoryServer(records, {
  tailStart = Math.max(0, records.length - 20),
  pageSize = 25,
  historyId = 'h1',
  latestInit = null,
  eventCursor = 7,
  // Issue #2030 (stage 2 PR B): `GET .../history_index` — `{ turns: [messageId], compactions: [...] }`
  // (turn ids; offsets are record indices, matching this fake's `b:<record index>` cursors) or an
  // Error-with-status / `null` to answer 404 like a server without the endpoint.
  index = null,
  indexError = null,
  // Issue #2030 (stage 3 PR 3C-1): `around=` / `around_id=` / `after=` windows (3A semantics, in records):
  // `[snap_back(anchor - aroundBefore), anchor + aroundAfter)` where snap_back moves the start back to the
  // nearest `user` record (a turn boundary); `after=` pages of `afterPageSize` records stopping at `until`.
  aroundBefore = 6,
  aroundAfter = 3,
  afterPageSize = 5,
  // Positions (record indices, ascending) a window start may snap back to; default: the nearest `user` record.
  snapTo = null,
} = {}) {
  const requests = []

  function slice(from, to) {
    return {
      messages: records.slice(from, to),
      start_cursor: `b:${from}`,
      has_more_before: from > 0,
      history_id: historyId,
      snapped: true,
      total_bytes: records.length, // one "byte" per record keeps cursor offsets and sizes comparable
    }
  }

  async function handle(url) {
    requests.push(url)
    if (String(url).split('?')[0].endsWith('/history_index')) {
      if (index === null || indexError) {
        const err = new Error(indexError?.message || 'not found')
        err.status = indexError?.status || 404
        throw err
      }
      const position = new Map(records.map((r, i) => [r.message_id, i]))
      return {
        history_id: historyId,
        total_bytes: records.length,
        turns: index.turns.map(id => [`b:${position.get(id)}`, id, position.get(id)]),
        compactions: index.compactions || [],
        event_cursor: eventCursor,
        epoch: 'e1',
      }
    }
    const query = String(url).split('?')[1] || ''
    const params = new URLSearchParams(query)
    if (params.get('window') === 'tail') {
      return { ...slice(tailStart, records.length), latest_init: latestInit, event_cursor: eventCursor, epoch: 'e1' }
    }
    if (params.has('before')) {
      const cursor = params.get('before')
      const match = /^b:(\d+)$/.exec(cursor)
      if (!match) {
        const err = new Error('cursor_invalid')
        err.status = 409
        throw err
      }
      const end = Number(match[1])
      return slice(Math.max(0, end - pageSize), end)
    }
    if (params.has('around') || params.has('around_id')) {
      let anchor
      if (params.has('around')) {
        const m = /^b:(\d+)$/.exec(params.get('around'))
        if (!m || Number(m[1]) >= records.length) throw Object.assign(new Error('cursor_invalid'), { status: 409 })
        anchor = Number(m[1])
      } else {
        const id = params.get('around_id')
        anchor = records.findIndex(r => r.message_id === id || r.tool_use_id === id ||
          (r.metadata?.tool_uses || []).some(t => t.id === id))
        if (anchor < 0) throw Object.assign(new Error('message_not_found'), { status: 404 })
      }
      let from = Math.max(0, anchor - aroundBefore)
      if (snapTo) {
        let best = 0
        for (const position of snapTo) { if (position <= from) best = position; else break }
        from = best
      } else {
        while (from > 0 && records[from].type !== 'user') from--
      }
      const to = Math.min(records.length, anchor + aroundAfter)
      return { ...slice(from, to), end_cursor: `b:${to}`, anchor_cursor: `b:${anchor}`, has_more_after: to < records.length }
    }
    if (params.has('after')) {
      const from = Number(/^b:(\d+)$/.exec(params.get('after'))?.[1])
      const limit = params.has('until') ? Number(/^b:(\d+)$/.exec(params.get('until'))[1]) : records.length
      if (!(from >= 0) || from > limit) throw Object.assign(new Error('cursor_invalid'), { status: 409 })
      const to = Math.min(limit, from + afterPageSize)
      return { ...slice(from, to), end_cursor: `b:${to}`, has_more_after: to < limit }
    }
    throw new Error(`unexpected history request: ${url}`)
  }

  return { handle, requests }
}
