import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { apiGet } from '../utils/api'
import { useSessionStore } from './session'

/**
 * Links Store — agent-registered persistent named links per session.
 *
 * Issue #1530: The register_link MCP tool allows agents to register named URLs
 * that appear in the Links panel of the right sidebar. Links survive page
 * refresh, compaction, session reset, and server restart.
 *
 * Link shape: { label: string, url: string, registered_at: string (ISO) }
 */
export const useLinksStore = defineStore('links', () => {
  // ========== STATE ==========

  // Links per session (sessionId -> Link[])
  const linksBySession = ref(new Map())

  // ========== GETTERS ==========

  function linksForSession(sessionId) {
    return linksBySession.value.get(sessionId) || []
  }

  function linkCountForSession(sessionId) {
    return linksForSession(sessionId).length
  }

  const currentLinks = computed(() => {
    const sessionStore = useSessionStore()
    const sid = sessionStore.currentSessionId
    return sid ? linksForSession(sid) : []
  })

  const currentLinkCount = computed(() => currentLinks.value.length)

  // ========== ACTIONS ==========

  async function loadLinks(sessionId) {
    if (!sessionId) return
    try {
      const data = await apiGet(`/api/sessions/${sessionId}/links`)
      linksBySession.value = new Map(linksBySession.value)
      linksBySession.value.set(sessionId, data.links || [])
    } catch (err) {
      console.error(`Failed to load links for session ${sessionId}:`, err)
    }
  }

  /**
   * Upsert a link client-side (from link_registered poll event).
   * Matches by exact label; replaces if found, appends if new.
   */
  function addLink(sessionId, link) {
    if (!sessionId || !link) return
    const existing = linksBySession.value.get(sessionId) || []
    const idx = existing.findIndex(l => l.label === link.label)
    let updated
    if (idx >= 0) {
      updated = [...existing]
      updated[idx] = link
    } else {
      updated = [...existing, link]
    }
    linksBySession.value = new Map(linksBySession.value)
    linksBySession.value.set(sessionId, updated)
  }

  function clearLinks(sessionId) {
    if (!sessionId) return
    linksBySession.value = new Map(linksBySession.value)
    linksBySession.value.delete(sessionId)
  }

  return {
    // state
    linksBySession,
    // getters
    linksForSession,
    linkCountForSession,
    currentLinks,
    currentLinkCount,
    // actions
    loadLinks,
    addLink,
    clearLinks,
  }
})
