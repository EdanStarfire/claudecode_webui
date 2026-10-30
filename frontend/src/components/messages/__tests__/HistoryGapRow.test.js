import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/vue'
import HistoryGapRow from '../HistoryGapRow.vue'

// Issue #2030 (stage 3 PR 3C-1): the not-loaded stretch between an island and the live end.

describe('HistoryGapRow', () => {
  it('shows the size and both actions; the buttons emit', async () => {
    const { emitted } = render(HistoryGapRow, { props: { approxBytes: 19 * 1024 * 1024 } })
    expect(screen.getByTestId('history-gap-label').textContent).toBe('About 19 MB of history not loaded')
    await fireEvent.click(screen.getByText('Load newer'))
    await fireEvent.click(screen.getByText('Jump to latest'))
    expect(emitted()['load-newer']).toHaveLength(1)
    expect(emitted()['jump-latest']).toHaveLength(1)
  })

  it('without a size it still says history is not loaded', () => {
    render(HistoryGapRow, { props: { approxBytes: null } })
    expect(screen.getByTestId('history-gap-label').textContent).toBe('History not loaded')
  })

  it('loading disables Load newer (Jump to latest stays usable) and shows a spinner', () => {
    const { container } = render(HistoryGapRow, { props: { approxBytes: 1000, loading: true } })
    expect(screen.getByTestId('history-gap-load-newer').disabled).toBe(true)
    expect(screen.getByTestId('history-gap-jump-latest').disabled).toBe(false)
    expect(container.querySelector('.spinner-border')).toBeTruthy()
  })

  it('shows an error without hiding the actions', () => {
    render(HistoryGapRow, { props: { approxBytes: 1000, error: 'boom' } })
    expect(screen.getByTestId('history-gap-error').textContent).toBe('boom')
    expect(screen.getByTestId('history-gap-load-newer').disabled).toBe(false)
  })

  it('a full island (interim cap) disables Load newer and says so', () => {
    render(HistoryGapRow, { props: { approxBytes: 1000, full: true } })
    expect(screen.getByTestId('history-gap-load-newer').disabled).toBe(true)
    expect(screen.getByTestId('history-gap-label').textContent).toContain('full')
  })
})
