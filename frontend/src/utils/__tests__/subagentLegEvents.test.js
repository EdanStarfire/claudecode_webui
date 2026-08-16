import { describe, it, expect } from 'vitest'
import { buildLegEventRuns } from '@/utils/subagentLegEvents'

describe('buildLegEventRuns (#1746 follow-up: interleave narration and tool usage chronologically)', () => {
  it('alternates thought/tool/tool/message/tool per the real chronology, not two stacked lists', () => {
    const narration = [
      { content: 'Starting the task.', timestamp: 100 },
      { content: 'Wrapping up now.', timestamp: 400 },
    ]
    const tools = [
      { id: 'tc-1', timestamp: 200 },
      { id: 'tc-2', timestamp: 250 },
      { id: 'tc-3', timestamp: 500 },
    ]

    const runs = buildLegEventRuns(narration, tools)

    expect(runs.map(r => r.kind)).toEqual(['narration', 'tool', 'narration', 'tool'])
    expect(runs[0].items.map(m => m.content)).toEqual(['Starting the task.'])
    expect(runs[1].items.map(t => t.id)).toEqual(['tc-1', 'tc-2'])
    expect(runs[2].items.map(m => m.content)).toEqual(['Wrapping up now.'])
    expect(runs[3].items.map(t => t.id)).toEqual(['tc-3'])
  })

  it('drops narration entries with no renderable content — the live-path placeholder sentinel', () => {
    const narration = [
      { content: 'Real text.', timestamp: 100 },
      { content: 'Assistant response', timestamp: 150 }, // live-path placeholder for tool-only segments
    ]
    const runs = buildLegEventRuns(narration, [])
    expect(runs).toHaveLength(1)
    expect(runs[0].items.map(m => m.content)).toEqual(['Real text.'])
  })

  it('drops narration entries with no renderable content — the reload-path empty string', () => {
    const narration = [
      { content: '', timestamp: 100 },
      { content: null, timestamp: 150 },
    ]
    const runs = buildLegEventRuns(narration, [])
    expect(runs).toHaveLength(0)
  })

  it('keeps thinking-only entries even with no text content', () => {
    const narration = [{ content: '', thinking: 'Considering the approach...', timestamp: 100 }]
    const runs = buildLegEventRuns(narration, [])
    expect(runs).toHaveLength(1)
    expect(runs[0].items[0].thinking).toBe('Considering the approach...')
  })

  it('falls back to created_at for tool call timestamps when timestamp is absent', () => {
    const tools = [{ id: 'tc-1', created_at: 100 }]
    const runs = buildLegEventRuns([], tools)
    expect(runs).toHaveLength(1)
    expect(runs[0].kind).toBe('tool')
  })

  it('returns an empty array for no narration and no tools', () => {
    expect(buildLegEventRuns([], [])).toEqual([])
    expect(buildLegEventRuns(null, null)).toEqual([])
  })

  it('single-kind input produces a single run, not one run per item', () => {
    const narration = [
      { content: 'One.', timestamp: 100 },
      { content: 'Two.', timestamp: 200 },
      { content: 'Three.', timestamp: 300 },
    ]
    const runs = buildLegEventRuns(narration, [])
    expect(runs).toHaveLength(1)
    expect(runs[0].items).toHaveLength(3)
  })
})
