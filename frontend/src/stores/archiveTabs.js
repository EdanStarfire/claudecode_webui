import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiGet, getAuthToken } from '../utils/api'

/**
 * Archive Tabs Store — issue #1263.
 *
 * Session-scoped Map pattern mirroring diff.js/proxy.js/resource.js: listing
 * metadata for the 5 new archive-viewer tabs (proxy logs, queue, attachments,
 * history, schedules) is loaded once per archive view via loadAllTabs(), while
 * actual file/log content is fetched on-demand only when a user expands/clicks
 * the relevant item.
 */
export const useArchiveTabsStore = defineStore('archiveTabs', () => {
  // ========== STATE ==========

  // Per-session active tab (sessionId -> tab name) — survives the
  // onActivated/onDeactivated KeepAlive cycle the same way SessionView.vue
  // already relies on for messages. Session-scoped (not a single global ref)
  // so switching between two different archived sessions' views doesn't leak
  // one session's tab selection into the other's default landing pane.
  const activeTabBySession = ref(new Map())

  // sessionId -> { projectId, archiveId } — remembered so the on-demand
  // content loaders below don't need projectId passed on every call.
  const archiveContext = ref(new Map())

  // Listing metadata per session (sessionId -> data), populated by loadAllTabs().
  const proxyLogsBySession = ref(new Map())     // -> [{name, size_bytes, line_count}]
  const queueBySession = ref(new Map())         // -> [queue entries]
  const attachmentsBySession = ref(new Map())   // -> [{name, size_bytes}]
  const historyBySession = ref(new Map())       // -> [{name, size_bytes, mtime}]
  const schedulesBySession = ref(new Map())     // -> {schedules, history}

  // True while loadAllTabs() is in flight for a session.
  const loadingBySession = ref(new Map())

  // On-demand content cache for history file text.
  // `${sessionId}:${filename}` -> { content, loading, error }
  const historyContentCache = ref(new Map())

  // ========== GETTERS ==========

  function proxyLogsForSession(sessionId) {
    return proxyLogsBySession.value.get(sessionId) || []
  }

  function queueForSession(sessionId) {
    return queueBySession.value.get(sessionId) || []
  }

  /**
   * queue.jsonl is an append-only event log (enqueue + status records), not a
   * list of item states — reconstruct one entry per queue_id, final status
   * wins. Shared by ArchiveQueueTab.vue (row rendering) and ArchiveTabBar.vue
   * (count badge) so the two never disagree on "how many queued messages".
   */
  function reconstructedQueueForSession(sessionId) {
    const raw = queueForSession(sessionId)
    const byId = new Map()
    for (const record of raw) {
      if (record.type === 'enqueue') {
        byId.set(record.queue_id, {
          queue_id: record.queue_id,
          content: record.content,
          created_at: record.created_at,
          status: 'pending',
        })
      } else if (record.type === 'status' && byId.has(record.queue_id)) {
        byId.get(record.queue_id).status = record.status
      }
    }
    return Array.from(byId.values()).sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
  }

  function attachmentsForSession(sessionId) {
    return attachmentsBySession.value.get(sessionId) || []
  }

  function historyForSession(sessionId) {
    return historyBySession.value.get(sessionId) || []
  }

  function schedulesForSession(sessionId) {
    return schedulesBySession.value.get(sessionId) || { schedules: [], history: [] }
  }

  function isLoadingTabs(sessionId) {
    return loadingBySession.value.get(sessionId) || false
  }

  function activeTabForSession(sessionId) {
    return activeTabBySession.value.get(sessionId) || 'messages'
  }

  function historyContent(sessionId, archiveId, filename) {
    return historyContentCache.value.get(`${sessionId}:${archiveId}:${filename}`) || null
  }

  // ========== ACTIONS ==========

  function setActiveTab(sessionId, tab) {
    activeTabBySession.value.set(sessionId, tab)
    activeTabBySession.value = new Map(activeTabBySession.value)
  }

  /**
   * Load listing metadata for all 5 tabs in parallel, once, on archive-view mount.
   * These are cheap metadata-only calls (unlike the content loaders below).
   */
  async function loadAllTabs(sessionId, projectId, archiveId) {
    if (!sessionId || !projectId || !archiveId) return

    archiveContext.value.set(sessionId, { projectId, archiveId })
    archiveContext.value = new Map(archiveContext.value)

    loadingBySession.value.set(sessionId, true)
    loadingBySession.value = new Map(loadingBySession.value)

    const base = `/api/projects/${projectId}/archives/${sessionId}/${archiveId}`

    try {
      const [proxyLogs, queue, attachments, history, schedules] = await Promise.all([
        apiGet(`${base}/proxy-logs`).catch((e) => { console.error('Failed to load archive proxy logs:', e); return [] }),
        apiGet(`${base}/queue`).catch((e) => { console.error('Failed to load archive queue:', e); return [] }),
        apiGet(`${base}/attachments`).catch((e) => { console.error('Failed to load archive attachments:', e); return [] }),
        apiGet(`${base}/history`).catch((e) => { console.error('Failed to load archive history:', e); return [] }),
        apiGet(`${base}/schedules`).catch((e) => { console.error('Failed to load archive schedules:', e); return { schedules: [], history: [] } }),
      ])

      proxyLogsBySession.value.set(sessionId, proxyLogs || [])
      proxyLogsBySession.value = new Map(proxyLogsBySession.value)

      queueBySession.value.set(sessionId, queue || [])
      queueBySession.value = new Map(queueBySession.value)

      attachmentsBySession.value.set(sessionId, attachments || [])
      attachmentsBySession.value = new Map(attachmentsBySession.value)

      historyBySession.value.set(sessionId, history || [])
      historyBySession.value = new Map(historyBySession.value)

      schedulesBySession.value.set(sessionId, schedules || { schedules: [], history: [] })
      schedulesBySession.value = new Map(schedulesBySession.value)
    } finally {
      loadingBySession.value.set(sessionId, false)
      loadingBySession.value = new Map(loadingBySession.value)
    }
  }

  /**
   * Fetch a chunk of lines from an archived proxy log (head/tail/load-more).
   * Returns {lines, total_lines, start_line, end_line}, or null on failure.
   */
  async function loadProxyLogContent(sessionId, archiveId, logName, { offset = 0, limit = 100, fromEnd = false } = {}) {
    const ctx = archiveContext.value.get(sessionId)
    if (!ctx) return null
    try {
      return await apiGet(
        `/api/projects/${ctx.projectId}/archives/${sessionId}/${archiveId}/proxy-logs/${encodeURIComponent(logName)}`,
        { params: { offset, limit, from_end: fromEnd } }
      )
    } catch (e) {
      console.error(`Failed to load proxy log content for ${logName}:`, e)
      return null
    }
  }

  /**
   * Fetch (and cache) markdown content for an archived history file.
   */
  async function loadHistoryContent(sessionId, archiveId, filename) {
    const ctx = archiveContext.value.get(sessionId)
    if (!ctx) return null

    const key = `${sessionId}:${archiveId}:${filename}`
    const cached = historyContentCache.value.get(key)
    if (cached && !cached.error) return cached.content

    historyContentCache.value.set(key, { content: null, loading: true, error: null })
    historyContentCache.value = new Map(historyContentCache.value)

    try {
      const result = await apiGet(
        `/api/projects/${ctx.projectId}/archives/${sessionId}/${archiveId}/history/${encodeURIComponent(filename)}`
      )
      historyContentCache.value.set(key, { content: result.content, loading: false, error: null })
      historyContentCache.value = new Map(historyContentCache.value)
      return result.content
    } catch (e) {
      console.error(`Failed to load history content for ${filename}:`, e)
      historyContentCache.value.set(key, { content: null, loading: false, error: e.message })
      historyContentCache.value = new Map(historyContentCache.value)
      return null
    }
  }

  /**
   * Build the download URL for an archived attachment (token appended, same
   * pattern as resource.js's getDownloadUrl/getResourceUrl).
   */
  function getAttachmentDownloadUrl(sessionId, archiveId, filename) {
    const ctx = archiveContext.value.get(sessionId)
    if (!ctx) return null
    let url = `/api/projects/${ctx.projectId}/archives/${sessionId}/${archiveId}/attachments/${encodeURIComponent(filename)}`
    const token = getAuthToken()
    if (token) {
      url += `?token=${encodeURIComponent(token)}`
    }
    return url
  }

  /**
   * Clear all archive-tab state for a session (called from SessionView.vue's
   * onDeactivated/onUnmounted teardown, alongside clearArchiveMessages/clearResources).
   */
  function clearArchiveTabs(sessionId) {
    activeTabBySession.value.delete(sessionId)
    activeTabBySession.value = new Map(activeTabBySession.value)

    archiveContext.value.delete(sessionId)
    archiveContext.value = new Map(archiveContext.value)

    proxyLogsBySession.value.delete(sessionId)
    proxyLogsBySession.value = new Map(proxyLogsBySession.value)

    queueBySession.value.delete(sessionId)
    queueBySession.value = new Map(queueBySession.value)

    attachmentsBySession.value.delete(sessionId)
    attachmentsBySession.value = new Map(attachmentsBySession.value)

    historyBySession.value.delete(sessionId)
    historyBySession.value = new Map(historyBySession.value)

    schedulesBySession.value.delete(sessionId)
    schedulesBySession.value = new Map(schedulesBySession.value)

    loadingBySession.value.delete(sessionId)
    loadingBySession.value = new Map(loadingBySession.value)

    for (const key of Array.from(historyContentCache.value.keys())) {
      if (key.startsWith(`${sessionId}:`)) historyContentCache.value.delete(key)
    }
    historyContentCache.value = new Map(historyContentCache.value)
  }

  return {
    // State
    activeTabBySession,
    archiveContext,
    proxyLogsBySession,
    queueBySession,
    attachmentsBySession,
    historyBySession,
    schedulesBySession,

    // Getters
    activeTabForSession,
    proxyLogsForSession,
    queueForSession,
    reconstructedQueueForSession,
    attachmentsForSession,
    historyForSession,
    schedulesForSession,
    isLoadingTabs,
    historyContent,

    // Actions
    setActiveTab,
    loadAllTabs,
    loadProxyLogContent,
    loadHistoryContent,
    getAttachmentDownloadUrl,
    clearArchiveTabs,
  }
})
