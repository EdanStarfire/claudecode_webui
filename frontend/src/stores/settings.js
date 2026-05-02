import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'

export const useSettingsStore = defineStore('settings', () => {
  // per-area in-memory drafts: areaKey → { fieldKey: value }
  const drafts = ref(new Map())
  // areas with unsaved changes
  const dirtyAreas = ref(new Set())
  // blocked navigation target (route object) waiting on dirty-guard resolution
  const pendingNavigation = ref(null)
  // mobile sidebar expanded state
  const sidebarExpanded = ref(false)
  // sidebar search query
  const searchQuery = ref('')

  // ---- computed ----

  const hasDirtyAreas = computed(() => dirtyAreas.value.size > 0)

  // ---- beforeunload ----

  function _onBeforeUnload(e) {
    e.preventDefault()
    e.returnValue = ''
  }

  watch(hasDirtyAreas, (dirty) => {
    if (dirty) {
      window.addEventListener('beforeunload', _onBeforeUnload)
    } else {
      window.removeEventListener('beforeunload', _onBeforeUnload)
    }
  })

  // ---- draft actions ----

  function getDraft(areaKey) {
    if (!drafts.value.has(areaKey)) {
      drafts.value.set(areaKey, {})
    }
    return drafts.value.get(areaKey)
  }

  function setField(areaKey, fieldKey, value) {
    if (!drafts.value.has(areaKey)) {
      drafts.value.set(areaKey, {})
    }
    drafts.value.get(areaKey)[fieldKey] = value
    dirtyAreas.value = new Set([...dirtyAreas.value, areaKey])
  }

  function markClean(areaKey) {
    dirtyAreas.value.delete(areaKey)
    dirtyAreas.value = new Set(dirtyAreas.value)
    drafts.value.delete(areaKey)
    drafts.value = new Map(drafts.value)
  }

  function discardDraft(areaKey) {
    markClean(areaKey)
  }

  // ---- navigation guard ----

  function requestNavigation(route) {
    if (!hasDirtyAreas.value) return false
    pendingNavigation.value = route
    return true
  }

  // action: 'apply' | 'discard' | 'cancel'
  function confirmNavigation(action) {
    if (action === 'cancel') {
      pendingNavigation.value = null
      return
    }
    if (action === 'discard') {
      for (const key of [...dirtyAreas.value]) {
        discardDraft(key)
      }
    }
    // 'apply' — caller is responsible for saving before calling this
    const route = pendingNavigation.value
    pendingNavigation.value = null
    return route
  }

  function setSidebarExpanded(value) {
    sidebarExpanded.value = value
  }

  function setSearchQuery(value) {
    searchQuery.value = value
  }

  return {
    drafts,
    dirtyAreas,
    pendingNavigation,
    sidebarExpanded,
    searchQuery,
    hasDirtyAreas,
    getDraft,
    setField,
    markClean,
    discardDraft,
    requestNavigation,
    confirmNavigation,
    setSidebarExpanded,
    setSearchQuery,
  }
})
