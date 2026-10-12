import { describe, it, expect } from 'vitest'
import { taskListCheckboxPlugin } from '../taskListCheckbox.js'

function makeState(nodes) {
  return { tree: { nodes, frontmatter: {}, meta: {} } }
}

function runPlugin(nodes) {
  const plugin = taskListCheckboxPlugin()
  const state = makeState(nodes)
  plugin.post(state)
  return state.tree.nodes
}

describe('taskListCheckboxPlugin', () => {
  it('replaces a genuine unchecked checkbox node with an unchecked span', () => {
    const nodes = [
      ['input', { class: 'task-list-item-checkbox', type: 'checkbox', ':disabled': 'true' }],
    ]
    const result = runPlugin(nodes)
    expect(result[0][0]).toBe('span')
    expect(result[0][1].class).toBe('task-list-checkbox')
    expect(result[0][1]['aria-label']).toBe('unchecked')
  })

  it('replaces a genuine checked checkbox node with a checked span', () => {
    const nodes = [
      ['input', {
        class: 'task-list-item-checkbox',
        type: 'checkbox',
        ':disabled': 'true',
        ':checked': 'true',
      }],
    ]
    const result = runPlugin(nodes)
    expect(result[0][0]).toBe('span')
    expect(result[0][1].class).toBe('task-list-checkbox task-list-checkbox-checked')
    expect(result[0][1]['aria-label']).toBe('checked')
  })

  it('does not forward any original prop into the replacement span', () => {
    const nodes = [
      ['input', {
        class: 'task-list-item-checkbox',
        type: 'checkbox',
        ':disabled': 'true',
        ':checked': 'true',
        'data-evil': 'payload',
      }],
    ]
    const result = runPlugin(nodes)
    expect(Object.keys(result[0][1]).sort()).toEqual(['aria-label', 'class', 'role'])
  })

  it('security regression guard: leaves a hand-typed-HTML forgery untouched (carries the $ marker)', () => {
    const nodes = [
      ['input', {
        $: { html: 1, block: 0 },
        class: 'task-list-item-checkbox',
        type: 'checkbox',
        disabled: '',
      }],
    ]
    const result = runPlugin(nodes)
    expect(result[0][0]).toBe('input')
  })

  it('leaves an input with type checkbox but a different class untouched', () => {
    const nodes = [['input', { class: 'something-else', type: 'checkbox' }]]
    const result = runPlugin(nodes)
    expect(result[0][0]).toBe('input')
  })

  it('leaves an input with no class untouched', () => {
    const nodes = [['input', { type: 'checkbox' }]]
    const result = runPlugin(nodes)
    expect(result[0][0]).toBe('input')
  })

  it('leaves a non-input node untouched', () => {
    const nodes = [['span', { class: 'task-list-item-checkbox' }]]
    const result = runPlugin(nodes)
    expect(result[0][0]).toBe('span')
    expect(result[0][1].class).toBe('task-list-item-checkbox')
  })
})
