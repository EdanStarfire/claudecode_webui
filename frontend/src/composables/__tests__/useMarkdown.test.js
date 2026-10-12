import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { parse } from 'comark'
import { useMarkdownPlugins } from '../useMarkdown.js'

// Runs real markdown source through comark's actual parse pipeline (markdown-it
// tokenization + comark's own task-list plugin + our registered plugins), not a
// hand-built tree — this is what actually exercises the real `$`-marker mechanism
// that distinguishes a synthesized checkbox from hand-typed raw HTML.
function findNodes(nodes, predicate, found = []) {
  for (const node of nodes) {
    if (!Array.isArray(node) || node[0] == null) continue
    if (predicate(node)) found.push(node)
    findNodes(node.slice(2), predicate, found)
  }
  return found
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('useMarkdownPlugins — task list checkbox integration', () => {
  it('renders unchecked and checked task-list items as distinct spans, not blank input nodes', async () => {
    const markdown = '- [ ] open task\n- [x] done task\n'
    const tree = await parse(markdown, { plugins: useMarkdownPlugins() })

    const inputs = findNodes(tree.nodes, (n) => n[0] === 'input')
    expect(inputs).toEqual([])

    const spans = findNodes(tree.nodes, (n) => n[0] === 'span' && n[1]?.class?.includes('task-list-checkbox'))
    expect(spans).toHaveLength(2)

    const [openSpan, doneSpan] = spans
    expect(openSpan[1].class).toBe('task-list-checkbox')
    expect(openSpan[1]['aria-label']).toBe('unchecked')
    expect(doneSpan[1].class).toBe('task-list-checkbox task-list-checkbox-checked')
    expect(doneSpan[1]['aria-label']).toBe('checked')
  })

  it('still fully strips a hand-typed raw <input> checkbox (security regression guard)', async () => {
    const markdown = 'Plain: <input type="checkbox" disabled>\n'
    const tree = await parse(markdown, { plugins: useMarkdownPlugins() })

    const inputs = findNodes(tree.nodes, (n) => n[0] === 'input')
    expect(inputs).toEqual([])
    const spans = findNodes(tree.nodes, (n) => n[0] === 'span' && n[1]?.class?.includes('task-list-checkbox'))
    expect(spans).toEqual([])
  })

  it('still fully strips a hand-typed raw <input> forging the exact task-list class/type/disabled attrs', async () => {
    const markdown = 'Forged: <input type="checkbox" class="task-list-item-checkbox" disabled>\n'
    const tree = await parse(markdown, { plugins: useMarkdownPlugins() })

    const inputs = findNodes(tree.nodes, (n) => n[0] === 'input')
    expect(inputs).toEqual([])
    const spans = findNodes(tree.nodes, (n) => n[0] === 'span' && n[1]?.class?.includes('task-list-checkbox'))
    expect(spans).toEqual([])
  })
})
