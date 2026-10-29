import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import { mount } from '@vue/test-utils'
import MessageSearchBar from '@/components/messages/MessageSearchBar.vue'

// Issue #2030: the scope indicator and "Load more results" must never be squeezed beside the input
// (on a 390 px phone the scope text pushed the bar off the left edge and wrapped to four lines).
function makeSearch(overrides = {}) {
  return {
    isOpen: ref(true), query: ref('Turn 5'), matches: ref([]), currentIndex: ref(-1), currentMatch: ref(null),
    notFound: ref(false), phase: ref('local'), hasMore: ref(false), loadingMore: ref(false), percent: ref(null),
    loadedOnly: ref(false),
    open() {}, close() {}, next() {}, prev() {}, loadMore: vi.fn(),
    ...overrides,
  }
}

const match = (i) => ({ messageId: `m${i}`, toolId: null, field: 'content', matchStart: 0, occurrenceIndex: 0 })

describe('MessageSearchBar scope indicator layout (issue #2030)', () => {
  it('renders the loaded-only indicator in its own row below the controls, with a long and a short form', () => {
    const wrapper = mount(MessageSearchBar, { props: { search: makeSearch({ loadedOnly: ref(true) }) } })
    const controlsRow = wrapper.find('.message-search-row')
    const scope = wrapper.find('[data-testid="search-history-scope"]')
    expect(scope.exists()).toBe(true)
    // Sibling of the controls row (not inside it) and after it in document order.
    expect(controlsRow.element.contains(scope.element)).toBe(false)
    expect(scope.element.parentElement).toBe(controlsRow.element.parentElement)
    expect(controlsRow.element.nextElementSibling).toBe(scope.element)
    expect(scope.find('.scope-long').text()).toBe('Searching loaded messages only')
    expect(scope.find('.scope-short').text()).toBe('Loaded only')
    // The input and the three controls stay in the one row.
    expect(controlsRow.findAll('button')).toHaveLength(3)
    expect(controlsRow.find('input').exists()).toBe(true)
  })

  it('renders no indicator when the search covers the whole history', () => {
    const wrapper = mount(MessageSearchBar, { props: { search: makeSearch() } })
    expect(wrapper.find('[data-testid="search-history-scope"]').exists()).toBe(false)
  })
})

describe('MessageSearchBar server search states (issue #2030 stage 3)', () => {
  it('shows "Searching… X %" until the first result arrives', () => {
    const wrapper = mount(MessageSearchBar, { props: { search: makeSearch({ phase: ref('searching'), percent: ref(40) }) } })
    expect(wrapper.find('.message-search-count').text()).toBe('Searching… 40 %')
  })

  it('shows "No results" for an exhausted empty search', () => {
    const wrapper = mount(MessageSearchBar, { props: { search: makeSearch({ phase: ref('server') }) } })
    expect(wrapper.find('.message-search-count').text()).toBe('No results')
  })

  it('shows "N+" and a Load more button on the row below the controls while more pages exist', async () => {
    const search = makeSearch({
      phase: ref('server'), matches: ref([match(1), match(2)]), currentIndex: ref(0), hasMore: ref(true),
    })
    const wrapper = mount(MessageSearchBar, { props: { search } })
    expect(wrapper.find('.message-search-count').text()).toBe('1 of 2+')
    const more = wrapper.find('[data-testid="search-load-more"]')
    expect(more.text()).toBe('Load more results')
    expect(wrapper.find('.message-search-row').element.contains(more.element)).toBe(false)
    await more.trigger('click')
    expect(search.loadMore).toHaveBeenCalledTimes(1)
  })

  it('disables the button and shows progress while a page is loading; exhausted drops "+" and the button', async () => {
    const search = makeSearch({
      phase: ref('server'), matches: ref([match(1)]), currentIndex: ref(0), hasMore: ref(true), loadingMore: ref(true), percent: ref(12),
    })
    const wrapper = mount(MessageSearchBar, { props: { search } })
    const more = wrapper.find('[data-testid="search-load-more"]')
    expect(more.text()).toBe('Searching… 12 %')
    expect(more.attributes('disabled')).toBeDefined()
    search.hasMore.value = false
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="search-load-more"]').exists()).toBe(false)
    expect(wrapper.find('.message-search-count').text()).toBe('1 of 1')
  })
})
