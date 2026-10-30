// Match-finding for the message-list search feature (issue #2012). Knows nothing about
// Vue/DOM/the virtualizer. `findMatches` searches the loaded messages of the message store;
// `searchServer` (issue #2030 stage 3) searches the whole history through the server and
// returns the same match descriptors, so the UI composable/components handle both alike.
import { api } from '@/utils/api'

export const MIN_QUERY_LENGTH = 2

const SNIPPET_RADIUS = 40

function toSearchableString(value, { stringifyObjects = false } = {}) {
  if (typeof value === 'string') return value
  if (stringifyObjects && value != null && typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return null
    }
  }
  return null
}

function buildSnippet(text, start, length) {
  const from = Math.max(0, start - SNIPPET_RADIUS)
  const to = Math.min(text.length, start + length + SNIPPET_RADIUS)
  return text.slice(from, to)
}

function resolveToolCall(toolCalls, toolIndex, toolUseId) {
  if (!toolCalls || !toolIndex) return null
  const idx = toolIndex.get(toolUseId)
  return idx !== undefined ? toolCalls[idx] : null
}

// Stored message records only ever carry `message_id` (minted once by the backend's
// `_mint_message_id()`) — `.id` is never set on a top-level message record, only on nested
// tool_use content blocks. Exported so MessageList.vue's indexMaps/messageIdIndex can resolve
// identity the same way, instead of maintaining its own independent copy of this fallback.
export function resolveMessageId(message) {
  return message.id || message.message_id || null
}

/**
 * Find every case-insensitive substring match for `query` across a session's loaded messages
 * and tool calls, in chronological store order. Returns an ordered array of match descriptors.
 */
export function findMatches(sessionId, query, { messageStore } = {}) {
  const trimmed = (query || '').trim()
  if (!sessionId || !messageStore || trimmed.length < MIN_QUERY_LENGTH) return []

  const lowerQuery = trimmed.toLowerCase()
  const matches = []
  // Issue #2012: a row can contain several matches (same field repeated, or several fields on
  // one message/tool) — `occurrenceIndex` counts, in emission order, how many prior matches
  // share this messageId, so the DOM highlighter (highlightMatch.js) can skip to the same
  // occurrence the user is actually navigating to instead of always re-highlighting the first
  // one found in the row.
  const occurrenceByMessageId = new Map()

  function pushFieldMatches(messageId, toolId, field, rawText) {
    if (typeof rawText !== 'string' || !rawText) return
    const lowerText = rawText.toLowerCase()
    let idx = lowerText.indexOf(lowerQuery)
    while (idx !== -1) {
      const occurrenceIndex = occurrenceByMessageId.get(messageId) || 0
      occurrenceByMessageId.set(messageId, occurrenceIndex + 1)
      matches.push({
        messageId,
        toolId,
        field,
        matchStart: idx,
        matchLength: trimmed.length,
        snippet: buildSnippet(rawText, idx, trimmed.length),
        occurrenceIndex,
      })
      idx = lowerText.indexOf(lowerQuery, idx + 1)
    }
  }

  // Issue #2030 stage 3 PR 3C-1: the loaded history is the live suffix plus a jumped-to island
  // (the store's union getters; plain stores/tests fall back to the suffix maps).
  const messages = messageStore.loadedMessages
    ? messageStore.loadedMessages(sessionId)
    : (messageStore.messagesBySession?.get(sessionId) || [])
  const toolCalls = messageStore.toolCallsBySession?.get(sessionId)
  const toolIndex = messageStore.toolIndexBySession?.get(sessionId)

  for (const message of messages) {
    const messageId = resolveMessageId(message)
    pushFieldMatches(messageId, null, 'content', toSearchableString(message.content))
    pushFieldMatches(
      messageId,
      null,
      'thinking',
      toSearchableString(message.metadata?.thinking_content)
    )

    const toolUses = message.metadata?.tool_uses || []
    for (const toolUse of toolUses) {
      const toolId = toolUse.id
      const toolCall = messageStore.findLoadedToolCall
        ? messageStore.findLoadedToolCall(sessionId, toolId)
        : resolveToolCall(toolCalls, toolIndex, toolId)
      if (!toolCall) continue

      pushFieldMatches(messageId, toolId, 'tool_name', toSearchableString(toolCall.name))
      pushFieldMatches(
        messageId,
        toolId,
        'tool_input',
        toSearchableString(toolCall.input, { stringifyObjects: true })
      )
      pushFieldMatches(
        messageId,
        toolId,
        'tool_result',
        toSearchableString(toolCall.result?.content, { stringifyObjects: true })
      )
    }
  }

  return matches
}

// ---- whole-history server search (issue #2030, stage 3) ----

// Random per tab / page load, sent on EVERY /search request (continuations included): the server
// supersedes a client's older query on a session when the same client starts a newer one, and
// never touches another tab's searches.
const CLIENT_ID = (typeof crypto !== 'undefined' && crypto.randomUUID)
  ? crypto.randomUUID()
  : `c${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`

export function getSearchClientId() {
  return CLIENT_ID
}

/** A request this long without an answer is treated like a failed endpoint (the server caps a page at ~2 s). */
export const SEARCH_REQUEST_TIMEOUT_MS = 20000

/** Server result row -> the match descriptor `findMatches` produces (+ `cursor`/`sizeHint`). */
export function toMatchDescriptor(result) {
  return {
    messageId: result.message_id,
    toolId: result.tool_id ?? null,
    field: result.field,
    matchStart: result.match_start,
    matchLength: result.match_length,
    snippet: result.snippet,
    occurrenceIndex: result.occurrence_index,
    cursor: result.cursor ?? null,
    sizeHint: typeof result.size_hint === 'number' ? result.size_hint : null,
  }
}

/**
 * One newest-first page of the server's whole-history search. `searchUrl` is the history
 * endpoint's `/search` sibling (live session or archive). Resolves to
 * `{ superseded, matches, nextCursor, exhausted, scannedFraction, historyId }`; rejects with the
 * API error (`status` 400/404/409/503/0) — or the caller's own abort — untouched, so the caller
 * can map each to its fallback. A request that gets no answer within the timeout rejects with
 * `err.timedOut`.
 */
export async function searchServer(searchUrl, query, cursor, { signal, limit } = {}) {
  const params = [`q=${encodeURIComponent((query || '').trim())}`, `client_id=${encodeURIComponent(CLIENT_ID)}`]
  if (cursor) params.push(`cursor=${encodeURIComponent(cursor)}`)
  if (limit) params.push(`limit=${limit}`)
  const timeout = new AbortController()
  const timer = setTimeout(() => timeout.abort(), SEARCH_REQUEST_TIMEOUT_MS)
  const fetchSignal = signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal
  try {
    const data = await api.get(`${searchUrl}?${params.join('&')}`, { signal: fetchSignal, priority: 'low' })
    return {
      superseded: !!data.superseded,
      matches: (data.results || []).map(toMatchDescriptor),
      nextCursor: data.next_cursor ?? null,
      exhausted: !!data.exhausted,
      scannedFraction: typeof data.scanned_fraction === 'number' ? data.scanned_fraction : null,
      historyId: data.history_id ?? null,
    }
  } catch (err) {
    if (!signal?.aborted && timeout.signal.aborted) {
      const timedOut = new Error('Search timed out')
      timedOut.timedOut = true
      throw timedOut
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}
