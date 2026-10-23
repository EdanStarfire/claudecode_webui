import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/vue'
import SandboxSubSectionWidget from '@/components/configuration/fields/SandboxSubSectionWidget.vue'

function lastEmit(r) {
  const e = r.emitted()['update:value']
  return e[e.length - 1][0]
}

describe('SandboxSubSectionWidget ignoreViolations', () => {
  it('emits {pattern: [substrings]} for a new row', async () => {
    const r = render(SandboxSubSectionWidget, { props: { value: {} } })
    await fireEvent.click(screen.getByText('+ Add ignore rule'))
    await fireEvent.update(screen.getByLabelText('Violation substrings'), 'a, b')
    expect(lastEmit(r).ignoreViolations).toEqual({ '*': ['a', 'b'] })
    await fireEvent.update(screen.getByLabelText('Command pattern'), 'npm')
    expect(lastEmit(r).ignoreViolations).toEqual({ npm: ['a', 'b'] })
  })

  it('migrates legacy file/network and shows a note', () => {
    const r = render(SandboxSubSectionWidget, {
      props: { value: { ignoreViolations: { file: '/etc/hosts, /tmp', network: 'x.com' } } },
    })
    expect(lastEmit(r).ignoreViolations).toEqual({ '*': ['/etc/hosts', '/tmp', 'x.com'] })
    expect(screen.getByTestId('ignore-violations-migrated')).toBeTruthy()
  })

  it('renders new-shape values without migrating', () => {
    const r = render(SandboxSubSectionWidget, {
      props: { value: { ignoreViolations: { git: ['x'] } } },
    })
    expect(r.emitted()['update:value']).toBeUndefined()
    expect(screen.getByLabelText('Command pattern').value).toBe('git')
  })

  it('removes a row', async () => {
    const r = render(SandboxSubSectionWidget, {
      props: { value: { ignoreViolations: { git: ['x'] } } },
    })
    await fireEvent.click(screen.getAllByText('×')[0])
    expect(lastEmit(r).ignoreViolations).toEqual({})
  })
})
