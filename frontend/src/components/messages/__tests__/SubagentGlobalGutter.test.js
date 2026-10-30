import { describe, it, expect } from 'vitest'
import { nextTick, reactive } from 'vue'
import { renderWithStores } from '@/test-utils/render'
import { useMessageStore } from '@/stores/message'
import SubagentGlobalGutter from '../SubagentGlobalGutter.vue'

// Issue #2030 (stage 3 PR 3C-2): chips of legs launched before the loaded history. Clamped legs
// share their interval's top, so their chips stack; each must be separately visible, clickable
// (44 px hit areas that do not cover each other) and named after its agent.

const SESSION = 'session-gutter'

function mountGutter(count) {
  const laneOffsets = reactive(new Map())
  const rendered = renderWithStores(SubagentGlobalGutter, {
    props: { sessionId: SESSION, laneOffsets },
  })
  const store = useMessageStore(rendered.pinia)
  for (let i = 0; i < count; i++) {
    store.applyTaskLifecycleFrame(SESSION, 'task_started',
      { task_id: `task-${i}`, tool_use_id: `launch-${i}`, description: `Agent ${i}` }, 1700000000 + i)
  }
  return { ...rendered, laneOffsets, store }
}

async function withOffsets(ctx, count, earlier = true) {
  for (let i = 0; i < count; i++) ctx.laneOffsets.set(`task-${i}:0`, { top: 0, bottom: 5000, height: 5000, launchedEarlier: earlier })
  await nextTick()
}

const chips = (container) => [...container.querySelectorAll('[data-testid="lane-launched-earlier"]')]

describe('SubagentGlobalGutter: launched-earlier chips', () => {
  for (const n of [3, 5]) {
    it(`${n} clamped legs at the same top: distinct offsets 44 px apart or more, distinct names, each click targets its own agent`, async () => {
      const ctx = mountGutter(n)
      await withOffsets(ctx, n)
      const found = chips(ctx.container)
      expect(found).toHaveLength(n)
      const tops = found.map(c => parseFloat(c.style.top))
      expect(new Set(tops).size).toBe(n)
      const sorted = [...tops].sort((a, b) => a - b)
      for (let i = 1; i < sorted.length; i++) expect(sorted[i] - sorted[i - 1]).toBeGreaterThanOrEqual(44)
      expect(new Set(found.map(c => c.getAttribute('aria-label'))).size).toBe(n)
      for (let i = 0; i < n; i++) expect(found.some(c => c.getAttribute('aria-label') === `Agent ${i}: launched earlier — jump`)).toBe(true)
      found.forEach(c => c.click())
      const jumps = ctx.emitted()['jump-launch'].map(args => args[0].taskId).sort()
      expect(jumps).toEqual(Array.from({ length: n }, (_, i) => `task-${i}`))
    })
  }

  it('more than five clamped legs draw five chips and a +k count on the last one', async () => {
    const ctx = mountGutter(8)
    await withOffsets(ctx, 8)
    expect(chips(ctx.container)).toHaveLength(5)
    const more = ctx.container.querySelectorAll('.chip-more')
    expect(more).toHaveLength(1)
    expect(more[0].textContent).toBe('+3')
  })

  it('a single clamped leg keeps the ordinary chip pitch; full lanes are unchanged', async () => {
    const ctx = mountGutter(2)
    ctx.laneOffsets.set('task-0:0', { top: 0, bottom: 5000, height: 5000, launchedEarlier: true })
    ctx.laneOffsets.set('task-1:0', { top: 100, bottom: 400, height: 300 })
    await nextTick()
    const all = [...ctx.container.querySelectorAll('.lane-chip')]
    expect(all.map(c => parseFloat(c.style.top)).sort((a, b) => a - b)).toEqual([8, 36]) // 8 + slot * 28
    expect(chips(ctx.container)).toHaveLength(1)
  })

  it('only clamped lanes and lanes spanning the gap row draw a visible lane line; ordinary lanes stay chip-only', async () => {
    const ctx = mountGutter(3)
    ctx.laneOffsets.set('task-0:0', { top: 0, bottom: 5000, height: 5000, launchedEarlier: true })
    ctx.laneOffsets.set('task-1:0', { top: 100, bottom: 4000, height: 3900, crossesGap: true })
    ctx.laneOffsets.set('task-2:0', { top: 200, bottom: 400, height: 200 })
    await nextTick()
    const lanesWithLine = [...ctx.container.querySelectorAll('.gutter-lane')].map(l => l.classList.contains('lane-line'))
    expect(lanesWithLine).toEqual([true, true, false])
  })
})
