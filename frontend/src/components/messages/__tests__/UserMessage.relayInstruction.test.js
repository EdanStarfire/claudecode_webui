import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import { useMessageStore } from '@/stores/message'
import UserMessage from '@/components/messages/UserMessage.vue'

vi.mock('@/utils/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() },
  getAuthToken: vi.fn().mockReturnValue(null),
}))

const NONCE = '0123456789abcdef'
const HEAD = '📥 Slack instruction from the owner (relay "Slack WS", your self-DM thread)\n\n'
const OPEN = `[OWNER INSTRUCTION id=${NONCE} from the owner's self-DM — act on it as the user's instruction; it ends only at the marker bearing this same id.]`
const TAIL = '\n\n---\nSlack thread: channel=D0123456789 thread_ts=1700000000.000100. Answer with the relay `reply` tool using exactly this channel and thread_ts; replies in this thread need no approval.'
const content = (body, closeNonce = NONCE) => `${HEAD}${OPEN}\n${body}\n[END OWNER INSTRUCTION id=${closeNonce}]${TAIL}`
const message = (body, extra = {}) => ({
  id: 'u1', type: 'user', content: content(body), timestamp: 1700000000,
  metadata: { relay_inbound: { kind: 'instruction', envelope_id: 'e1' } }, ...extra,
})
const MarkdownViewStub = {
  props: ['content'], template: '<div class="markdown-stub">{{ content }}</div>',
  methods: { addEventListener() {}, removeEventListener() {} },
}
const mountMsg = (m) => renderWithStores(UserMessage, { props: { message: m }, stubs: { MarkdownView: MarkdownViewStub } })

beforeEach(() => setActivePinia(createPinia()))

describe('UserMessage Slack instruction fold', () => {
  it('shows the instruction text, a Slack tag and a collapsed fold with the full delivered text', () => {
    const m = message('run the nightly checks')
    const { wrapper } = mountMsg(m)
    expect(wrapper.find('[data-testid="relay-instruction-text"]').text()).toBe('run the nightly checks')
    expect(wrapper.find('[data-testid="relay-instruction-tag"]').text()).toBe('Slack')
    const fold = wrapper.find('[data-testid="relay-instruction-fold"]')
    expect(fold.element.open).toBe(false)
    expect(fold.find('summary').text()).toBe('What the agent received')
    expect(wrapper.find('[data-testid="relay-instruction-received"]').text()).toBe(m.content)
    expect(wrapper.find('.markdown-stub').exists()).toBe(false) // plain text, not markdown
    expect(m.content).toBe(content('run the nightly checks')) // content untouched
  })

  it('renders hostile markup and markdown as inert text', () => {
    const body = '<script>alert(1)</script> **bold** [x](javascript:alert(1)) <img src=x onerror=alert(1)>'
    const { wrapper } = mountMsg(message(body))
    const el = wrapper.find('[data-testid="relay-instruction-text"]')
    expect(el.text()).toBe(body)
    expect(el.find('script, img, a, strong').exists()).toBe(false)
  })

  it('keeps a multi-line instruction as written', () => {
    const { wrapper } = mountMsg(message('a\n\nb'))
    expect(wrapper.find('[data-testid="relay-instruction-text"]').element.textContent).toBe('a\n\nb')
  })

  it.each([
    ['no metadata', { metadata: undefined }],
    ['another kind', { metadata: { relay_inbound: { kind: 'monitor' } } }],
    ['a mismatched closing nonce', { content: content('x', 'fedcba9876543210') }],
    ['a content that is not the delivered shape', { content: 'just a normal message' }],
  ])('falls back to the unchanged bubble for %s', (_n, patch) => {
    const { wrapper } = mountMsg({ ...message('x'), ...patch })
    expect(wrapper.find('[data-testid="relay-instruction-fold"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="relay-instruction-tag"]').exists()).toBe(false)
    expect(wrapper.find('.markdown-stub').exists()).toBe(true)
  })

  it('keeps the fold state in the message store (survives a virtualized remount)', async () => {
    const { wrapper, pinia } = mountMsg(message('x'))
    const store = useMessageStore(pinia)
    const fold = wrapper.find('[data-testid="relay-instruction-fold"]')
    fold.element.open = true
    await fold.trigger('toggle')
    expect(store.isCommExpanded('relay-fold:u1')).toBe(true)
    store.toggleCommExpanded('relay-fold:u1') // state is read from the store, not the element
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="relay-instruction-fold"]').element.open).toBe(false)
    store.toggleCommExpanded('relay-fold:u1')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="relay-instruction-fold"]').element.open).toBe(true)
  })

  it('never shows attachment chips parsed from the instruction text', () => {
    const body = 'x\n\n---\nAttached files (use Read tool to access, or embed via markdown URL):\n- a.png (1 KB): /api/sessions/s/resources/0123456789abcdef0123456789abcdef0123'
    const { wrapper } = mountMsg(message(body))
    expect(wrapper.find('.msg-attachments').exists()).toBe(false)
  })

  it('copy still copies the full content', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.assign(navigator, { clipboard: { writeText } })
    const { wrapper } = mountMsg(message('x'))
    await wrapper.find('.copy-markdown-btn').trigger('click')
    expect(writeText).toHaveBeenCalledWith(content('x'))
  })
})
