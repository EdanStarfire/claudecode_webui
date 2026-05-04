<template>
  <div class="settings-section">
    <SettingsToolbar
      title="MCP Servers"
      :chips="toolbarChips"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    />

    <div v-if="!entity" class="section-loading">Loading…</div>

    <div v-else-if="isNotApplicable" class="section-na">
      <p>MCP Servers configuration is not applicable for a <strong>{{ entity.area }}</strong> profile.</p>
    </div>

    <div v-else class="section-body">
      <McpServerPanel
        :mcp-server-ids="mergedConfig.mcp_server_ids || []"
        :claude-ai-enabled="mergedConfig.enable_claudeai_mcp_servers !== false"
        :local-enabled="!mergedConfig.strict_mcp_config"
        :runtime-servers="[]"
        :session-active="false"
        :claude-ai-field-state="fieldStates.enable_claudeai_mcp_servers"
        :local-field-state="fieldStates.strict_mcp_config"
        :server-id-field-states="serverIdFieldStates"
        @update:mcp-server-ids="v => handleField('mcp_server_ids', v)"
        @update:claude-ai-enabled="v => handleField('enable_claudeai_mcp_servers', v)"
        @update:local-enabled="v => handleField('strict_mcp_config', !v)"
        @reset-claude-ai="handleReset('enable_claudeai_mcp_servers')"
        @reset-local="handleReset('strict_mcp_config')"
        @reset-server="handleServerReset"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useTemplateStore } from '@/stores/template'
import { useProfileStore } from '@/stores/profile'
import SettingsToolbar from '../SettingsToolbar.vue'
import McpServerPanel from '../../configuration/McpServerPanel.vue'
import { useEditSectionFieldStates } from '@/composables/useEditSectionFieldStates'
import { useEditSectionReset } from '@/composables/useEditSectionReset'
import { FIELD_RESET } from '@/composables/fieldResetSentinel.js'

const PROFILE_AREA = 'mcp'
const SECTION_KEY  = 'mcp-servers'
const MCP_FIELDS   = [
  { key: 'enable_claudeai_mcp_servers' },
  { key: 'strict_mcp_config' },
]

const route = useRoute()
const settingsStore = useSettingsStore()
const templateStore = useTemplateStore()
const profileStore  = useProfileStore()

const isTemplateMode = computed(() => route.path.startsWith('/settings/template/'))
const isProfileMode  = computed(() => route.path.startsWith('/settings/profile/'))
const entityId       = computed(() => route.params.templateId || route.params.profileId || '')
const areaKey        = computed(() => {
  const prefix = isTemplateMode.value ? 'template' : 'profile'
  return `${prefix}:${entityId.value}:${SECTION_KEY}`
})

const entity = computed(() => {
  if (isTemplateMode.value) return templateStore.getTemplate(entityId.value)
  if (isProfileMode.value)  return profileStore.getProfile(entityId.value)
  return null
})

const isNotApplicable = computed(() => isProfileMode.value && entity.value?.area !== PROFILE_AREA)

const baseConfig = computed(() => entity.value?.config || {})

const draft        = computed(() => settingsStore.getDraft(areaKey.value))
const isDirty      = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving       = ref(false)

const boundProfileId = computed(() => {
  if (!isTemplateMode.value) return null
  return entity.value?.profile_ids?.[PROFILE_AREA] || null
})
const boundProfile = computed(() => boundProfileId.value ? profileStore.getProfile(boundProfileId.value) : null)

const profileBase = computed(() => isTemplateMode.value && boundProfile.value?.config ? boundProfile.value.config : {})

const mergedConfig = computed(() => {
  const draftEntries = Object.entries(draft.value || {})
  const resetKeys = new Set(draftEntries.filter(([, v]) => v === FIELD_RESET).map(([k]) => k))
  const cleanDraft = Object.fromEntries(draftEntries.filter(([, v]) => v !== FIELD_RESET))
  const cleanBase = Object.fromEntries(Object.entries(baseConfig.value || {}).filter(([k]) => !resetKeys.has(k)))
  return { ...profileBase.value, ...cleanBase, ...cleanDraft }
})

const { fieldStates } = useEditSectionFieldStates({
  isTemplateMode,
  baseConfig,
  draft,
  boundProfile,
  schemaFields: MCP_FIELDS,
})
const { handleReset } = useEditSectionReset({ areaKey })

const serverIdFieldStates = computed(() => {
  const result = {}

  const draftVal = draft.value?.mcp_server_ids
  const isDraftReset = draftVal === FIELD_RESET
  const draftIds = (!isDraftReset && Array.isArray(draftVal)) ? new Set(draftVal) : null
  const baseIds = new Set(baseConfig.value?.mcp_server_ids || [])
  const profileIds = new Set(profileBase.value?.mcp_server_ids || [])
  const selfKind = isTemplateMode.value ? 'T' : 'P'
  const mergedIds = new Set(mergedConfig.value.mcp_server_ids || [])

  // mcp_server_ids is "managed here" when the template/profile has an explicit array
  // (even if empty), meaning it overrides what the profile would provide.
  const managedHere = !!(
    draftIds ||
    (!isDraftReset && baseConfig.value && 'mcp_server_ids' in baseConfig.value)
  )

  // Include baseIds so draft-excluded servers remain visible with ∅
  const allRelevantIds = new Set([...mergedIds, ...profileIds, ...baseIds])

  for (const id of allRelevantIds) {
    const isSelected = mergedIds.has(id)
    const profileHasIt = profileIds.has(id)

    if (!managedHere) {
      if (isTemplateMode.value && profileHasIt && isSelected) {
        result[id] = { kind: 'P', profileName: boundProfile.value?.name || 'Profile' }
      }
      continue
    }

    if (!isTemplateMode.value) {
      if (isSelected) result[id] = { kind: selfKind }
      continue
    }

    // Template mode, managed here: marker driven entirely by whether
    // template's decision matches the profile's.
    const differsFromProfile = isSelected !== profileHasIt
    if (differsFromProfile) {
      result[id] = { kind: selfKind, resettable: true }
    } else if (isSelected) {
      // Consistent and selected → inherited from profile
      result[id] = { kind: 'P', profileName: boundProfile.value?.name || 'Profile' }
    } else {
      // Consistent and unselected → no upstream source
      result[id] = { kind: 'EMPTY' }
    }
  }
  return result
})

const toolbarChips = computed(() => {
  if (!isTemplateMode.value || !boundProfileId.value) return []
  const section = route.params.section || SECTION_KEY
  return [{
    type: 'P',
    label: boundProfile.value?.name || 'Profile',
    to: `/settings/profile/${boundProfileId.value}/${section}`,
  }]
})

function handleField(key, value) {
  settingsStore.setField(areaKey.value, key, value)
}

function handleServerReset(serverId) {
  const current = new Set(mergedConfig.value.mcp_server_ids || [])
  if ((profileBase.value?.mcp_server_ids || []).includes(serverId)) {
    current.add(serverId)
  } else {
    current.delete(serverId)
  }
  handleField('mcp_server_ids', [...current])
}

async function handleSave() {
  if (!entity.value || !isDirty.value) return
  saving.value = true
  try {
    const d = { ...draft.value }
    const keysToDelete = Object.keys(d).filter(k => d[k] === FIELD_RESET)
    for (const k of keysToDelete) delete d[k]

    if (keysToDelete.length > 0) {
      const newConfig = { ...(entity.value?.config || {}), ...d }
      for (const k of keysToDelete) delete newConfig[k]
      if (isTemplateMode.value) {
        await templateStore.updateTemplate(entityId.value, { config: newConfig })
      } else {
        await profileStore.updateProfile(entityId.value, { config: newConfig })
      }
    } else if (isTemplateMode.value) {
      await templateStore.updateTemplate(entityId.value, d)
    } else {
      const newConfig = { ...(entity.value?.config || {}), ...d }
      await profileStore.updateProfile(entityId.value, { config: newConfig })
    }
    settingsStore.markClean(areaKey.value)
  } catch (err) {
    console.error('Save failed:', err)
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  settingsStore.discardDraft(areaKey.value)
}

defineExpose({ save: handleSave })

onMounted(() => {
  if (isTemplateMode.value) { templateStore.fetchTemplates(); profileStore.fetchIfEmpty() }
  if (isProfileMode.value)  profileStore.fetchIfEmpty()
})
</script>

<style scoped>
.settings-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-loading,
.section-na {
  padding: 24px 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
  line-height: 1.6;
}

.section-na strong { color: var(--bs-emphasis-color); }

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
</style>
