import { describe, it, expect, vi } from 'vitest'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import BackgroundTasksSection from '@/components/tasks/BackgroundTasksSection.vue'
import { useSessionStore } from '@/stores/session'
import { useBackgroundTasksStore } from '@/stores/backgroundTasks'
import samples from '../../../../../backend/tests/fixtures/background_tasks_samples.json'

vi.mock('@/utils/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() },
  getAuthToken: vi.fn()
}))

function mountWith(tasks) {
  const { pinia } = renderWithStores(BackgroundTasksSection)
  useSessionStore(pinia).currentSessionId = 's1'
  useBackgroundTasksStore(pinia).setTasks('s1', tasks)
  return pinia
}

describe('BackgroundTasksSection (issue #2145)', () => {
  it('is hidden when empty', async () => {
    renderWithStores(BackgroundTasksSection)
    expect(screen.queryByTestId('background-tasks-section')).toBeNull()
  })

  it('shows count header, chip and description per row', async () => {
    mountWith([
      { task_id: 'a', task_type: 'local_bash', description: 'Run tests' },
      { task_id: 'b', task_type: 'local_agent', description: 'Verify fixes' },
    ])
    expect(await screen.findByText('Background (2)')).toBeTruthy()
    expect(screen.getByText('local_bash')).toBeTruthy()
    expect(screen.getByText('Run tests')).toBeTruthy()
  })

  it('renders duplicate descriptions as distinct rows keyed by task_id', async () => {
    mountWith([
      { task_id: 'a', task_type: 'local_bash', description: 'same' },
      { task_id: 'b', task_type: 'local_bash', description: 'same' },
    ])
    expect(await screen.findAllByTestId('background-task-row')).toHaveLength(2)
  })

  it('renders unknown task types generically with the raw type', async () => {
    mountWith([{ task_id: 'a', task_type: 'future_kind', description: 'x' }])
    const chip = await screen.findByText('future_kind')
    expect(chip.className).toContain('type-unknown')
  })

  it('multiline description: title carries full text; click toggles expand', async () => {
    const s3 = samples.find(s => s._sample.trim() === 'S3')
    const multi = { ...s3.metadata.init_data.tasks[0], description: 'line one\nline two' }
    mountWith([multi])
    const row = await screen.findByTestId('background-task-row')
    expect(row.getAttribute('title')).toBe('line one\nline two')
    expect(row.className).not.toContain('expanded')
    await fireEvent.click(row)
    expect(row.className).toContain('expanded')
  })

  it('renders all 16 tasks of the S2 sample', async () => {
    const s2 = samples.find(s => s._sample.trim() === 'S2')
    mountWith(s2.metadata.init_data.tasks)
    expect(await screen.findAllByTestId('background-task-row')).toHaveLength(16)
  })
})
