import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import AskUserQuestionToolHandler from '@/components/tools/AskUserQuestionToolHandler.vue'

function baseToolCall(overrides = {}) {
  return {
    id: 'use-askq-1',
    name: 'AskUserQuestion',
    input: { questions: [{ question: 'Which approach?', options: [{ label: 'Option A' }, { label: 'Option B' }] }] },
    status: 'completed',
    result: { error: false, content: 'ok' },
    answers: null,
    ...overrides
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('AskUserQuestionToolHandler', () => {
  it('renders answers from toolCall.answers as the primary source (#1774)', () => {
    renderWithStores(AskUserQuestionToolHandler, {
      props: { toolCall: baseToolCall({ answers: { 'Which approach?': 'Option A' } }) }
    })

    expect(screen.getByText('Which approach?')).toBeTruthy()
    expect(screen.getByText('Option A')).toBeTruthy()
    expect(screen.queryByText('No answers recorded.')).toBeNull()
  })

  it('shows "No answers recorded." when genuinely empty', () => {
    renderWithStores(AskUserQuestionToolHandler, {
      props: { toolCall: baseToolCall({ answers: null, result: { error: true, message: 'Permission denied' } }) }
    })

    expect(screen.getByText('No answers recorded.')).toBeTruthy()
  })

  it('renders multi-select and "Other" free-text answers as flattened strings', () => {
    renderWithStores(AskUserQuestionToolHandler, {
      props: {
        toolCall: baseToolCall({
          answers: { 'Which approach?': 'Option A, Option B, custom text' }
        })
      }
    })

    expect(screen.getByText('Option A, Option B, custom text')).toBeTruthy()
  })

  it('falls back to result.content.answers when toolCall.answers is absent (legacy/historical data)', () => {
    renderWithStores(AskUserQuestionToolHandler, {
      props: {
        toolCall: baseToolCall({
          answers: null,
          result: { error: false, content: { answers: { 'Which approach?': 'Option B' } } }
        })
      }
    })

    expect(screen.getByText('Option B')).toBeTruthy()
  })
})
