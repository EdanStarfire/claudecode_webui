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
    throw new Error(`unexpected history request: ${url}`)
  }

  return { handle, requests }
}
