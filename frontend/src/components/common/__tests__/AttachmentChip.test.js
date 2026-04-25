import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import AttachmentChip from '@/components/common/AttachmentChip.vue'

vi.mock('@/utils/fileTypes', () => ({
  getFileIcon: () => '📄',
  getFileIconByMimeType: () => '📄'
}))

describe('AttachmentChip', () => {
  it('renders filename and emits preview when clicked with resourceId', async () => {
    const user = userEvent.setup()
    const { emitted } = renderWithStores(AttachmentChip, {
      props: {
        filename: 'report.pdf',
        resourceId: 'res-1',
        sessionId: 'sess-1'
      }
    })

    expect(screen.getByText('report.pdf')).toBeTruthy()

    const chip = screen.getByTitle('Click to preview')
    await user.click(chip)

    expect(emitted().preview).toBeTruthy()
  })
})
