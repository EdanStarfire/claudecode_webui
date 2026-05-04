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

    <div v-else-if="isPermanentScheduleNA" class="section-na">
      <p>This schedule is bound to a permanent session. Only General settings apply.</p>
    </div>

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
import { useSessionStore } from '@/stores/session'
import SettingsToolbar from '../SettingsToolbar.vue'
import McpServerPanel from '../../configuration/McpServerPanel.vue'
import { useEditSectionFieldStates } from '@/composables/useEditSectionFieldStates'
import { useEditSectionReset } from '@/composables/useEditSectionReset'
import { FIELD_RESET } from '@/composables/fieldResetSentinel.js'
import { useScheduleStore } from '@/stores/schedule'
import { useScheduleSectionSave } from '@/composables/useScheduleSectionSave'

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
const sessionStore  = useSessionStore()
const scheduleStore = useScheduleStore()

const isTemplateMode = computed(() => route.path.startsWith('/settings/template/'))
const isProfileMode  = computed(() => route.path.startsWith('/settings/profile/'))
const isSessionMode  = computed(() => route.path.startsWith('/settings/session/'))
const isScheduleMode = computed(() => route.path.startsWith('/settings/schedule/'))
const entityId       = computed(() => route.params.sessionId || route.params.templateId || route.params.profileId || route.params.scheduleId || '')
const areaKey        = computed(() => {
  if (isScheduleMode.value) return `schedule:${entityId.value}:${SECTION_KEY}`
  if (isSessionMode.value) return `session:${entityId.value}:${SECTION_KEY}`
  const prefix = isTemplateMode.value ? 'template' : 'profile'
  return `${prefix}:${entityId.value}:${SECTION_KEY}`
})

const entity = computed(() => {
  if (isScheduleMode.value)  return scheduleStore.getSchedule(entityId.value)
  if (isSessionMode.value)   return sessionStore.getSession(entityId.value)
  if (isTemplateMode.value)  return templateStore.getTemplate(entityId.value)
  if (isProfileMode.value)   return profileStore.getProfile(entityId.value)
  return null
})

const isPermanentScheduleNA = computed(() =>
  isScheduleMode.value && entity.value != null && !entity.value.session_config
)

const isNotApplicable = computed(() =>
  !isSessionMode.value && !isScheduleMode.value && isProfileMode.value && entity.value?.area !== PROFILE_AREA
)

const baseConfig = computed(() => {
  if (isScheduleMode.value) return entity.value?.session_config || {}
  return entity.value?.config || {}
})

const boundTemplateId = computed(() => isSessionMode.value ? (entity.value?.template_id || null) : null)
const boundTemplate   = computed(() => boundTemplateId.value ? templateStore.getTemplate(boundTemplateId.value) : null)
const templateBase    = computed(() => isSessionMode.value ? (boundTemplate.value?.config || {}) : {})

const draft = computed(() => settingsStore.getDraft(areaKey.value))
const isDirty = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving  = ref(false)

const boundProfileId = computed(() => {
  if (isSessionMode.value)   return boundTemplate.value?.profile_ids?.[PROFILE_AREA] || null
  if (!isTemplateMode.value) return null
  return entity.value?.profile_ids?.[PROFILE_AREA] || null
})
const boundProfile = computed(() => boundProfileId.value ? profileStore.getProfile(boundProfileId.value) : null)
const profileBase  = computed(() => (isSessionMode.value || isTemplateMode.value) ? (boundProfile.value?.config || {}) : {})

const mergedConfig = computed(() => {
  const draftEntries = Object.entries(draft.value || {})
  const resetKeys = new Set(draftEntries.filter(([, v]) => v === FIELD_RESET).map(([k]) => k))
  const cleanDraft = Object.fromEntries(draftEntries.filter(([, v]) => v !== FIELD_RESET))
  const cleanBase = Object.fromEntries(Object.entries(baseConfig.value || {}).filter(([k]) => !resetKeys.has(k)))
  return { ...profileBase.value, ...templateBase.value, ...cleanBase, ...cleanDraft }
})

const { fieldStates } = useEditSectionFieldStates({
  isTemplateMode, isSessionMode, isScheduleMode, baseConfig, draft,
  boundProfile, boundTemplate,
  schemaFields: MCP_FIELDS,
})
const { handleReset } = useEditSectionReset({ areaKey })

const saveScheduleSessionConfig = useScheduleSectionSave({
  scheduleId: entityId,
  legionId: computed(() => entity.value?.legion_id),
  ephemeralAgentId: computed(() => entity.value?.ephemeral_agent_id),
})

const serverIdFieldStates = computed(() => {
  const result = {}

  const draftVal = draft.value?.mcp_server_ids
  const isDraftReset = draftVal === FIELD_RESET
  const draftIds = (!isDraftReset && Array.isArray(draftVal)) ? new Set(draftVal) : null
  const baseIds = new Set(baseConfig.value?.mcp_server_ids || [])
  const profileIds = new Set(profileBase.value?.mcp_server_ids || [])
  const templateIds = new Set(templateBase.value?.mcp_server_ids || [])
  const selfKind = isSessionMode.value ? 'S' : (isTemplateMode.value ? 'T' : 'P')
  const mergedIds = new Set(mergedConfig.value.mcp_server_ids || [])

  const managedHere = !!(
    draftIds ||
    (!isDraftReset && baseConfig.value && 'mcp_server_ids' in baseConfig.value)
  )

  const allRelevantIds = new Set([...mergedIds, ...profileIds, ...templateIds, ...baseIds])

  for (const id of allRelevantIds) {
    const isSelected = mergedIds.has(id)
    const profileHasIt = profileIds.has(id)
    const templateHasIt = templateIds.has(id)

    if (!managedHere) {
      if (isSessionMode.value && templateHasIt && isSelected) {
        result[id] = { kind: 'T', templateName: boundTemplate.value?.name || 'Template' }
      } else if ((isTemplateMode.value || isSessionMode.value) && profileHasIt && isSelected) {
        result[id] = { kind: 'P', profileName: boundProfile.value?.name || 'Profile' }
      }
      continue
    }

    if (!isTemplateMode.value && !isSessionMode.value) {
      if (isSelected) result[id] = { kind: selfKind }
      continue
    }

    const differsFromProfile = isSelected !== profileHasIt
    if (differsFromProfile) {
      result[id] = { kind: selfKind, resettable: true }
    } else if (isSelected) {
      result[id] = { kind: 'P', profileName: boundProfile.value?.name || 'Profile' }
    } else {
      result[id] = { kind: 'EMPTY' }
    }
  }
  return result
})

const toolbarChips = computed(() => {
  if (isSessionMode.value) {
    const section = route.params.section || SECTION_KEY
    const chips = []
    if (boundTemplateId.value) {
      chips.push({ type: 'T', label: boundTemplate.value?.name || 'Template', to: `/settings/template/${boundTemplateId.value}/${section}` })
    }
    if (boundProfileId.value) {
      chips.push({ type: 'P', label: boundProfile.value?.name || 'Profile', to: `/settings/profile/${boundProfileId.value}/${section}` })
    } else if (boundTemplateId.value) {
      chips.push({ type: 'P', label: 'No profile', disabled: true, tooltip: 'This template has no profile binding' })
    }
    return chips
  }
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
  const upstreamHasIt = profileBase.value?.mcp_server_ids?.includes(serverId) ||
    (isSessionMode.value && templateBase.value?.mcp_server_ids?.includes(serverId))
  if (upstreamHasIt) {
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

    if (isScheduleMode.value) {
      const newSessionConfig = { ...(entity.value?.session_config || {}), ...d }
      for (const k of keysToDelete) delete newSessionConfig[k]
      await saveScheduleSessionConfig(newSessionConfig)
      settingsStore.markClean(areaKey.value)
      return
    }
    if (isSessionMode.value) {
      const newConfig = { ...(entity.value?.config || {}), ...d }
      for (const k of keysToDelete) delete newConfig[k]
      await sessionStore.patchSession(entityId.value, { config: newConfig })
    } else if (keysToDelete.length > 0) {
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

defineExpose({ save: handleSave, cancel: handleCancel })

onMounted(() => {
  if (isSessionMode.value)  { templateStore.fetchTemplates(); profileStore.fetchIfEmpty() }
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
