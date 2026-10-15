// Pure, framework-agnostic match-finding for the message-list search feature (issue #2012).
// Knows nothing about Vue/DOM/the virtualizer — only raw message-store data. This is the seam
// issue #2030 (tail-first loading) will swap to a backend-search call behind, without the UI
// composable/components changing.

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

  const messages = messageStore.messagesBySession?.get(sessionId) || []
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
      const toolCall = resolveToolCall(toolCalls, toolIndex, toolId)
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
