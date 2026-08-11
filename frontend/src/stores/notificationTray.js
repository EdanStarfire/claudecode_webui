import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getEventLabel } from '@/composables/useNotifications'

const STORAGE_KEY = 'webui-notification-tray'
const MAX_ENTRIES = 100

function loadEntries() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function persistEntries(entries) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(entries)) } catch {}
}

/**
 * Notification Tray Store - global, persistent list of actionable event
 * entries (Issue #1725). Mirrors the shape of stores/ui.js's
 * pushAlert/dismissAlert watchdog-alert pattern, but persisted across
 * sessions/restarts and capped at MAX_ENTRIES.
 */
export const useNotificationTrayStore = defineStore('notificationTray', () => {
  // ========== STATE ==========
  const entries = ref(loadEntries())

  // ========== COMPUTED ==========
  const unreadCount = computed(() => entries.value.length)

  // ========== ACTIONS ==========

  function addEntry(eventType, context = {}) {
    const { title, body } = getEventLabel(eventType, context)
    const id = `${eventType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const entry = {
      id,
      eventType,
      title,
      body,
      sessionId: context.sessionId || null,
      sessionName: context.sessionName || context.fromMinion || null,
      timestamp: Date.now(),
    }
    entries.value = [entry, ...entries.value].slice(0, MAX_ENTRIES)
    persistEntries(entries.value)
  }

  function dismissEntry(id) {
    entries.value = entries.value.filter(e => e.id !== id)
    persistEntries(entries.value)
  }

  function clearAll() {
    entries.value = []
    persistEntries(entries.value)
  }

  // Removes the entry and returns its sessionId so the caller can navigate there.
  function markReadAndNavigate(id) {
    const entry = entries.value.find(e => e.id === id)
    dismissEntry(id)
    return entry?.sessionId || null
  }

  // ========== RETURN ==========
  return {
    entries,
    unreadCount,
    addEntry,
    dismissEntry,
    clearAll,
    markReadAndNavigate,
  }
})
