import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import { mount } from '@vue/test-utils'
import MessageSearchBar from '@/components/messages/MessageSearchBar.vue'

// Issue #2030: the "still loading" scope indicator must never be squeezed beside the input (on a
// 390 px phone it pushed the bar off the left edge and wrapped to four lines).
function makeSearch({ scope }) {
  return {
    isOpen: ref(true), query: ref('Turn 5'), matches: ref([]), currentIndex: ref(-1), currentMatch: ref(null),
    notFound: ref(false), historyScope: ref(scope),
    open() {}, close() {}, next() {}, prev() {},
  }
}

describe('MessageSearchBar scope indicator layout (issue #2030)', () => {
  it('renders the indicator in its own row below the controls, with a long and a short form', () => {
    const wrapper = mount(MessageSearchBar, { props: { search: makeSearch({ scope: { percent: 38 } }) } })
    const controlsRow = wrapper.find('.message-search-row')
    const scope = wrapper.find('[data-testid="search-history-scope"]')
    expect(scope.exists()).toBe(true)
    // Sibling of the controls row (not inside it) and after it in document order.
    expect(controlsRow.element.contains(scope.element)).toBe(false)
    expect(scope.element.parentElement).toBe(controlsRow.element.parentElement)
    expect(controlsRow.element.nextElementSibling).toBe(scope.element)
    expect(scope.find('.scope-long').text()).toBe('Searching 38 % of history — still loading')
    expect(scope.find('.scope-short').text()).toBe('38 % loaded')
    // The input and the three controls stay in the one row.
    expect(controlsRow.findAll('button')).toHaveLength(3)
    expect(controlsRow.find('input').exists()).toBe(true)
  })

  it('falls back to a percentage-free label when the progress cannot be derived', () => {
    const wrapper = mount(MessageSearchBar, { props: { search: makeSearch({ scope: { percent: null } }) } })
    expect(wrapper.find('.scope-long').text()).toBe('Searching loaded history — still loading')
    expect(wrapper.find('.scope-short').text()).toBe('Partial history')
  })

  it('renders no indicator once the history is complete', () => {
    const wrapper = mount(MessageSearchBar, { props: { search: makeSearch({ scope: null }) } })
    expect(wrapper.find('[data-testid="search-history-scope"]').exists()).toBe(false)
  })
})
