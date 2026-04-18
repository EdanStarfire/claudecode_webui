/**
 * Profile Store
 *
 * Manages configuration profiles (the base layer in the 3-tier
 * Profile → Template → Session inheritance chain).
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/utils/api'

export const useProfileStore = defineStore('profile', () => {
  // ---- State ----
  const profiles = ref(new Map()) // profile_id -> profile object
  const loading = ref(false)
  const error = ref(null)
  const loaded = ref(false)

  // ---- Computed ----

  /** All profiles as an array */
  const allProfiles = computed(() => Array.from(profiles.value.values()))

  /** Profiles grouped by area */
  const profilesByArea = computed(() => {
    const byArea = {}
    for (const profile of profiles.value.values()) {
      if (!byArea[profile.area]) byArea[profile.area] = []
      byArea[profile.area].push(profile)
    }
    return byArea
  })

  /** Get profiles for a specific area */
  function profilesForArea(area) {
    return profilesByArea.value[area] ?? []
  }

  /** Get a single profile by ID */
  function getProfile(profileId) {
    return profiles.value.get(profileId) || null
  }

  // ---- Actions ----

  async function fetchProfiles(area = null) {
    loading.value = true
    error.value = null
    try {
      const params = area ? { area } : {}
      const data = await api.get('/api/profiles', { params })
      const list = data.profiles || []
      if (!area) {
        // Full refresh: replace entire map
        profiles.value.clear()
      }
      for (const profile of list) {
        profiles.value.set(profile.profile_id, profile)
      }
      if (!area) {
        loaded.value = true
      }
    } catch (err) {
      error.value = err.message || 'Failed to load profiles'
      console.error('Failed to fetch profiles:', err)
    } finally {
      loading.value = false
    }
  }

  async function fetchIfEmpty() {
    if (loaded.value && profiles.value.size > 0) return
    await fetchProfiles()
  }

  async function createProfile(name, area, config) {
    const profile = await api.post('/api/profiles', { name, area, config })
    profiles.value.set(profile.profile_id, profile)
    return profile
  }

  async function updateProfile(profileId, updates) {
    const profile = await api.put(`/api/profiles/${profileId}`, updates)
    profiles.value.set(profile.profile_id, profile)
    return profile
  }

  async function deleteProfile(profileId) {
    const result = await api.delete(`/api/profiles/${profileId}`)
    if (result && result.deleted) {
      profiles.value.delete(profileId)
    }
    return result
  }

  return {
    profiles,
    loading,
    error,
    loaded,
    allProfiles,
    profilesByArea,
    profilesForArea,
    getProfile,
    fetchProfiles,
    fetchIfEmpty,
    createProfile,
    updateProfile,
    deleteProfile,
  }
})
