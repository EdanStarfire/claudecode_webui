/**
 * Issue #1746 (stage: subagents) / #1765: extracts Task/Agent tool-call anchors from every
 * segment of a merged assistant-turn run, deduplicated by tool_use_id across the WHOLE run —
 * not just the last segment.
 *
 * Root cause of #1765: the previous logic (AssistantMessage.vue's old `taskToolCalls`
 * computed) read Task/Agent tool calls only from the last segment of a merged run. When two
 * subagents launched in different segments of the same merged run, the earlier one's card
 * silently dropped out the moment a later segment became "last" — Vue destroyed its
 * SubagentTimeline instance (its `:key` left the array) and mounted the new one in roughly the
 * same visual slot. Scanning every segment (and rendering each anchor at its own segment's
 * position, not deferred to the end) removes the eviction mechanism entirely: an anchor, once
 * rendered, is never destroyed by a later segment's own Task/Agent call.
 *
 * @param {Array<Array<{id: string, name: string}>>} segmentsEnrichedToolCalls - one array of
 *   enriched tool calls per segment, in segment order.
 * @returns {Array<Array<{id: string, name: string}>>} one array of Task/Agent anchors per
 *   segment (same length/order as input), each entry appearing in exactly one segment's array.
 */
export function computeSubagentAnchorsBySegment(segmentsEnrichedToolCalls) {
  const seen = new Set()
  return segmentsEnrichedToolCalls.map(toolCalls => {
    const anchors = []
    for (const tc of toolCalls) {
      if (tc.name !== 'Task' && tc.name !== 'Agent') continue
      if (seen.has(tc.id)) continue
      seen.add(tc.id)
      anchors.push(tc)
    }
    return anchors
  })
}
