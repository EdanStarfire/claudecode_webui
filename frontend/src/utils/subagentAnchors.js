/**
 * Issue #1746 (stage: subagents) / #1765: extracts subagent-leg-launch tool-call anchors from
 * every segment of a merged assistant-turn run, deduplicated by tool_use_id across the WHOLE
 * run — not just the last segment.
 *
 * Root cause of #1765 (as originally traced): the previous logic (AssistantMessage.vue's old
 * `taskToolCalls` computed) read Task/Agent tool calls only from the last segment of a merged
 * run. Scanning every segment (and rendering each anchor at its own segment's position, not
 * deferred to the end) removes that eviction mechanism entirely. (A second, independently
 * confirmed root cause — a metadata-merge bug in message.js's addMessage — was found and fixed
 * separately; both fixes are complementary, not alternatives.)
 *
 * Issue #1746 follow-up (real repro data): a subagent leg does not always launch via a tool
 * literally named 'Task' or 'Agent' — resuming a stopped/idle subagent happens via a
 * `SendMessage(to: "<agent name>")` call instead, and its `task_started` frame's tool_use_id is
 * that SendMessage call's own id, not a Task/Agent call's. `isLaunchAnchor` is therefore an
 * injected predicate (rather than a hardcoded name check) so the caller can recognize ANY
 * tool_use whose id resolves to a known task_id via the store, in addition to freshly-dispatched
 * Task/Agent calls that haven't resolved to a task_id yet.
 *
 * @param {Array<Array<{id: string, name: string}>>} segmentsEnrichedToolCalls - one array of
 *   enriched tool calls per segment, in segment order.
 * @param {(tc: {id: string, name: string}) => boolean} [isLaunchAnchor] - predicate deciding
 *   whether a tool call is a subagent-leg-launch anchor. Defaults to the name-only check
 *   ('Task'/'Agent') for callers (e.g. tests) that don't need store-backed resolution.
 * @returns {Array<Array<{id: string, name: string}>>} one array of launch anchors per segment
 *   (same length/order as input), each entry appearing in exactly one segment's array.
 */
export function computeSubagentAnchorsBySegment(segmentsEnrichedToolCalls, isLaunchAnchor = defaultIsLaunchAnchor) {
  const seen = new Set()
  return segmentsEnrichedToolCalls.map(toolCalls => {
    const anchors = []
    for (const tc of toolCalls) {
      if (!isLaunchAnchor(tc)) continue
      if (seen.has(tc.id)) continue
      seen.add(tc.id)
      anchors.push(tc)
    }
    return anchors
  })
}

function defaultIsLaunchAnchor(tc) {
  return tc.name === 'Task' || tc.name === 'Agent'
}
