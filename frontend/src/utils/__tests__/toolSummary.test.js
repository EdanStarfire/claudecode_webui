import { describe, it, expect } from 'vitest'
import { generateShortToolSummary } from '@/utils/toolSummary'

describe('generateShortToolSummary - Bash/Shell/Command', () => {
  it('shows description when present', () => {
    const summary = generateShortToolSummary({ name: 'Bash', input: { command: 'ls -la', description: 'List files' } })
    expect(summary).toBe('Bash: List files')
  })

  it('falls back to command when description is empty string', () => {
    const summary = generateShortToolSummary({ name: 'Bash', input: { command: 'ls -la', description: '' } })
    expect(summary).toBe('Bash: ls -la')
  })

  it('falls back to command when description is absent', () => {
    const summary = generateShortToolSummary({ name: 'Bash', input: { command: 'ls -la' } })
    expect(summary).toBe('Bash: ls -la')
  })

  it('truncates a long description at 60 chars with ellipsis', () => {
    const longDescription = 'a'.repeat(70)
    const summary = generateShortToolSummary({ name: 'Bash', input: { command: 'ls', description: longDescription } })
    expect(summary).toBe(`Bash: ${'a'.repeat(60)}...`)
  })

  it('applies description preference identically for Shell', () => {
    const summary = generateShortToolSummary({ name: 'Shell', input: { command: 'ls -la', description: 'List files' } })
    expect(summary).toBe('Shell: List files')
  })

  it('applies description preference identically for Command', () => {
    const summary = generateShortToolSummary({ name: 'Command', input: { command: 'ls -la', description: 'List files' } })
    expect(summary).toBe('Command: List files')
  })
})
