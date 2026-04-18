/**
 * useProfileSelector composable
 *
 * Shared logic for profile selector dropdowns in AdvancedSettingsPanel
 * and QuickSettingsPanel. Provides area-scoped profile listing, reading,
 * and updating via the profile store.
 */

import { useProfileStore } from '@/stores/profile'

export function useProfileSelector(props, emit) {
  const profileStore = useProfileStore()

  function profilesForArea(area) {
    return profileStore.profilesForArea(area)
  }

  function getProfileForArea(area) {
    return props.profileIds?.[area] || null
  }

  function setProfileForArea(area, profileId) {
    const updated = { ...(props.profileIds || {}) }
    if (profileId) {
      updated[area] = profileId
    } else {
      delete updated[area]
    }
    emit('update:profile-ids', updated)
  }

  return { profilesForArea, getProfileForArea, setProfileForArea }
}
