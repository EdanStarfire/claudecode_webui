import { onMounted, onUnmounted, unref } from 'vue'
import { useResourceStore } from '@/stores/resource'

export function useResourceImages(containerRef, sessionId) {
  const resourceStore = useResourceStore()
  const RESOURCE_URL_PATTERN = /\/api\/sessions\/[^/]+\/resources\/([^/?]+)/

  function handleClick(event) {
    const img = event.target
    if (img.tagName !== 'IMG') return

    const match = (img.getAttribute('src') || '').match(RESOURCE_URL_PATTERN)
    if (!match) return

    const resourceId = match[1]
    const sid = unref(sessionId)
    if (!sid) return

    const idx = resourceStore.resourcesForSession(sid).findIndex(r => r.resource_id === resourceId)
    if (idx >= 0) {
      resourceStore.openFullView(sid, idx)
    }
  }

  onMounted(() => {
    const el = unref(containerRef)
    if (el) {
      el.addEventListener('click', handleClick)
    }
  })

  onUnmounted(() => {
    const el = unref(containerRef)
    if (el) {
      el.removeEventListener('click', handleClick)
    }
  })
}
