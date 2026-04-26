import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api.js'

const DEFAULT_SINCE = () => Math.floor(Date.now() / 1000) - 3600  // last 1h

export const useAuditStore = defineStore('audit', () => {
  // ── Filters ─────────────────────────────────────────────────────────────
  const filters = ref({
    since: DEFAULT_SINCE(),
    until: null,
    sessionIds: [],        // [] means "all"
    projectId: null,
    eventTypes: [],        // [] means "all"
  })

  // ── Tab / view state ─────────────────────────────────────────────────────
  const activeTab = ref('turns')  // 'turns' | 'stream'

  // ── Turns tab data ────────────────────────────────────────────────────────
  const turns = ref([])
  const standalones = ref([])
  const turnsCursor = ref(null)
  const turnsLoading = ref(false)
  const turnsError = ref(null)

  // ── Stream tab data ───────────────────────────────────────────────────────
  const events = ref([])
  const eventsCursor = ref(null)
  const streamLoading = ref(false)
  const streamError = ref(null)
  const liveStreamEnabled = ref(true)

  // ── Expanded turn events ─────────────────────────────────────────────────
  const expandedTurnEvents = ref(new Map())  // turn_id → Event[]
  const expandedTurnLoading = ref(new Set())

  // ── Poll state ────────────────────────────────────────────────────────────
  const pollStatus = ref('idle')  // 'idle' | 'polling' | 'error'
  let _pollAbort = null

  // ── Computed ──────────────────────────────────────────────────────────────
  const hasData = computed(() => turns.value.length > 0 || events.value.length > 0)

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _filterParams() {
    const f = filters.value
    const p = {}
    if (f.since != null) p.since = f.since
    if (f.until != null) p.until = f.until
    if (f.sessionIds.length > 0) p.session_ids = f.sessionIds.join(',')
    if (f.projectId) p.project_id = f.projectId
    if (f.eventTypes.length > 0) p.event_types = f.eventTypes.join(',')
    return p
  }

  function _buildQuery(extra = {}) {
    return new URLSearchParams({ ..._filterParams(), ...extra }).toString()
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  async function fetchTurns() {
    turnsLoading.value = true
    turnsError.value = null
    try {
      const qs = _buildQuery({ limit: 50 })
      const data = await api.get(`/api/audit/turns?${qs}`)
      turns.value = data.turns || []
      standalones.value = data.standalones || []
      turnsCursor.value = data.next_cursor ?? null
    } catch (e) {
      turnsError.value = e.message || 'Failed to load turns'
    } finally {
      turnsLoading.value = false
    }
  }

  async function fetchEvents() {
    streamLoading.value = true
    streamError.value = null
    try {
      const qs = _buildQuery({ limit: 200 })
      const data = await api.get(`/api/audit/events?${qs}`)
      events.value = data.events || []
      eventsCursor.value = data.next_cursor ?? null
    } catch (e) {
      streamError.value = e.message || 'Failed to load events'
    } finally {
      streamLoading.value = false
    }
  }

  async function loadTurnEvents(turnId, sessionId) {
    if (expandedTurnLoading.value.has(turnId)) return
    expandedTurnLoading.value.add(turnId)
    try {
      const qs = new URLSearchParams({
        session_ids: sessionId,
        turn_id: turnId,
        limit: 200,
      }).toString()
      const data = await api.get(`/api/audit/events?${qs}`)
      expandedTurnEvents.value.set(turnId, data.events || [])
    } catch (e) {
      expandedTurnEvents.value.set(turnId, [])
    } finally {
      expandedTurnLoading.value.delete(turnId)
    }
  }

  function setFilters(newFilters) {
    filters.value = { ...filters.value, ...newFilters }
    turns.value = []
    standalones.value = []
    events.value = []
    expandedTurnEvents.value.clear()
  }

  function setActiveTab(tab) {
    activeTab.value = tab
  }

  function setTimeRange(seconds) {
    filters.value.since = Math.floor(Date.now() / 1000) - seconds
    filters.value.until = null
    turns.value = []
    standalones.value = []
    events.value = []
    expandedTurnEvents.value.clear()
  }

  async function startLivePoll() {
    if (pollStatus.value === 'polling') return
    stopLivePoll()

    pollStatus.value = 'polling'
    const abortController = { cancelled: false }
    _pollAbort = abortController

    const poll = async () => {
      while (!abortController.cancelled && liveStreamEnabled.value) {
        try {
          const cursor = eventsCursor.value ?? filters.value.since ?? DEFAULT_SINCE()
          const extra = { cursor, timeout: 25, limit: 100 }
          if (filters.value.sessionIds.length > 0) {
            extra.session_ids = filters.value.sessionIds.join(',')
          }
          if (filters.value.eventTypes.length > 0) {
            extra.event_types = filters.value.eventTypes.join(',')
          }
          const qs = new URLSearchParams(extra).toString()
          const data = await api.get(`/api/poll/audit?${qs}`)
          if (abortController.cancelled) break

          const newEvents = data.events || []
          if (newEvents.length > 0) {
            // Prepend new events (newest-first display)
            events.value = [...newEvents, ...events.value].slice(0, 500)
            eventsCursor.value = data.next_cursor ?? eventsCursor.value
          }
          pollStatus.value = 'polling'
        } catch (e) {
          if (abortController.cancelled) break
          pollStatus.value = 'error'
          await new Promise(r => setTimeout(r, 5000))
          pollStatus.value = 'polling'
        }
      }
      if (!abortController.cancelled) {
        pollStatus.value = 'idle'
      }
    }

    poll()
  }

  function stopLivePoll() {
    if (_pollAbort) {
      _pollAbort.cancelled = true
      _pollAbort = null
    }
    pollStatus.value = 'idle'
  }

  function toggleLiveStream() {
    liveStreamEnabled.value = !liveStreamEnabled.value
    if (liveStreamEnabled.value) {
      startLivePoll()
    } else {
      stopLivePoll()
    }
  }

  return {
    // State
    filters,
    activeTab,
    turns,
    standalones,
    turnsCursor,
    turnsLoading,
    turnsError,
    events,
    eventsCursor,
    streamLoading,
    streamError,
    liveStreamEnabled,
    expandedTurnEvents,
    expandedTurnLoading,
    pollStatus,
    hasData,
    // Actions
    fetchTurns,
    fetchEvents,
    loadTurnEvents,
    setFilters,
    setActiveTab,
    setTimeRange,
    startLivePoll,
    stopLivePoll,
    toggleLiveStream,
  }
})
