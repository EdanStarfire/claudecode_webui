import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import CopyLinkButton from '@/components/messages/CopyLinkButton.vue'

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

describe('CopyLinkButton (issue #2030 stage 3 PR 3F)', () => {
  it('copies the link of the open view for its message id and shows a check for two seconds', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn().mockResolvedValue()
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    window.location.hash = '#/session/s1/archive/a9'
    const wrapper = mount(CopyLinkButton, { props: { messageId: 'm-42' } })
    const button = wrapper.get('[data-testid="copy-message-link"]')
    expect(button.attributes('aria-label')).toBe('Copy link to message')
    await button.trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(writeText).toHaveBeenCalledWith(expect.stringMatching(/#\/session\/s1\/archive\/a9\?msg=m-42$/))
    expect(button.attributes('title')).toBe('Link copied!')
    await vi.advanceTimersByTimeAsync(2100)
    expect(button.attributes('title')).toBe('Copy link to message')
    vi.unstubAllGlobals()
  })

  it('falls back to execCommand where the async clipboard is missing (plain http) and still shows the check', async () => {
    vi.stubGlobal('navigator', {})
    document.execCommand = vi.fn(() => true)
    const wrapper = mount(CopyLinkButton, { props: { messageId: 'm-7' } })
    await wrapper.get('button').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(wrapper.get('button').attributes('title')).toBe('Link copied!')
    vi.unstubAllGlobals()
  })

  it('does not bubble the click to the message and stays quiet when copying fails', async () => {
    vi.stubGlobal('navigator', {})
    document.execCommand = vi.fn(() => false)
    const parentClick = vi.fn()
    const wrapper = mount({ components: { CopyLinkButton }, template: '<div @click="onClick"><CopyLinkButton message-id="m" /></div>', methods: { onClick: parentClick } })
    await wrapper.get('button').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(parentClick).not.toHaveBeenCalled()
    expect(wrapper.get('button').attributes('title')).toBe('Copy link to message')
    vi.unstubAllGlobals()
  })
})
