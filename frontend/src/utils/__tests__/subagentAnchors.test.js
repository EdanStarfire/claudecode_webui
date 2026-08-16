import { describe, it, expect } from 'vitest'
import { computeSubagentAnchorsBySegment } from '@/utils/subagentAnchors'

describe('computeSubagentAnchorsBySegment (#1765 eviction fix)', () => {
  it('preserves an earlier segment\'s Task anchor when a later segment in the same run launches another agent', () => {
    // Reproduces the exact #1765 scenario: segment 1 launches agent A, segment 2 (a later
    // segment merged into the same assistant-turn run) launches agent B. The OLD logic read
    // Task/Agent tool calls only from the LAST segment (segment 2), so agent A's anchor would
    // never appear at all — this assertion would fail under that logic.
    const segmentA = [{ id: 'toolu_agentA', name: 'Task' }]
    const segmentB = [{ id: 'toolu_agentB', name: 'Task' }]

    const result = computeSubagentAnchorsBySegment([segmentA, segmentB])

    expect(result).toHaveLength(2)
    expect(result[0]).toEqual([{ id: 'toolu_agentA', name: 'Task' }])
    expect(result[1]).toEqual([{ id: 'toolu_agentB', name: 'Task' }])
  })

  it('deduplicates the same tool_use_id if it appears in more than one segment\'s tool list', () => {
    const shared = { id: 'toolu_dup', name: 'Task' }
    const result = computeSubagentAnchorsBySegment([[shared], [shared]])

    expect(result[0]).toEqual([shared])
    expect(result[1]).toEqual([])
  })

  it('ignores non-Task/Agent tool calls', () => {
    const result = computeSubagentAnchorsBySegment([
      [{ id: 'toolu_1', name: 'Read' }, { id: 'toolu_2', name: 'Task' }],
    ])

    expect(result[0]).toEqual([{ id: 'toolu_2', name: 'Task' }])
  })

  it('recognizes both Task and Agent tool names', () => {
    const result = computeSubagentAnchorsBySegment([
      [{ id: 'toolu_1', name: 'Task' }, { id: 'toolu_2', name: 'Agent' }],
    ])

    expect(result[0]).toHaveLength(2)
  })

  it('returns an empty array per segment when there are no subagent tool calls', () => {
    const result = computeSubagentAnchorsBySegment([[{ id: 'toolu_1', name: 'Read' }], []])
    expect(result).toEqual([[], []])
  })

  it('handles three or more concurrent launches across three segments', () => {
    const result = computeSubagentAnchorsBySegment([
      [{ id: 'toolu_a', name: 'Task' }],
      [{ id: 'toolu_b', name: 'Task' }],
      [{ id: 'toolu_c', name: 'Task' }],
    ])
    expect(result.flat().map(a => a.id)).toEqual(['toolu_a', 'toolu_b', 'toolu_c'])
  })

  it('regression guard: the old last-segment-only logic would have dropped agent A entirely (#1765)', () => {
    const segmentA = [{ id: 'toolu_agentA', name: 'Task' }]
    const segmentB = [{ id: 'toolu_agentB', name: 'Task' }]
    const segments = [segmentA, segmentB]

    // The exact pre-fix logic from AssistantMessage.vue's old `taskToolCalls` computed:
    // `(lastSegmentView.enrichedToolCalls).filter(tc => tc.name === 'Task' || tc.name === 'Agent')`
    const oldLogicResult = segments[segments.length - 1].filter(tc => tc.name === 'Task' || tc.name === 'Agent')
    expect(oldLogicResult.map(a => a.id)).toEqual(['toolu_agentB']) // agent A silently missing

    // The new logic recovers agent A instead of evicting it.
    const newLogicResult = computeSubagentAnchorsBySegment(segments).flat()
    expect(newLogicResult.map(a => a.id)).toEqual(['toolu_agentA', 'toolu_agentB'])
  })
})
