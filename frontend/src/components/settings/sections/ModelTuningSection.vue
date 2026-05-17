<template>
  <div class="settings-section">
    <SettingsToolbar
      title="Model Tuning"
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
      <p>Model Tuning fields are not applicable for a <strong>{{ entity.area }}</strong> profile.</p>
      <p>This profile stores <strong>{{ AREA_LABEL[entity.area] || entity.area }}</strong> configuration.</p>
    </div>

    <div v-else class="section-body">
      <div v-if="tierValidationError" class="alert alert-warning py-1 px-2 mb-2" style="font-size:12px;">
        {{ tierValidationError }}
      </div>
      <FieldSection
        :fields="FIELD_SCHEMAS.model"
        :config="mergedConfig"
        :show-badges="true"
        :field-states="fieldStates"
        :show-include-toggle="false"
        @update:config="handleField"
        @reset="handleReset"
        @update:linked="handleLinkedField"
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
import FieldSection from '../../configuration/fields/FieldSection.vue'
import { FIELD_SCHEMAS } from '../../configuration/fields/fieldSchemas.js'
import { useEditSectionFieldStates } from '@/composables/useEditSectionFieldStates'
import { useEditSectionReset } from '@/composables/useEditSectionReset'
import { FIELD_RESET } from '@/composables/fieldResetSentinel.js'
import { useScheduleStore } from '@/stores/schedule'
import { useScheduleSectionSave } from '@/composables/useScheduleSectionSave'

const PROFILE_AREA = 'model'
const SECTION_KEY  = 'model-tuning'

const AREA_LABEL = {
  model: 'Model Tuning', permissions: 'Tools & Permissions',
  system_prompt: 'System Prompt', mcp: 'MCP Servers',
  isolation: 'Isolation', features: 'Features',
}

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

// Session mode: bound template and its config
const boundTemplateId = computed(() => {
  if (!isSessionMode.value) return null
  return entity.value?.template_id || null
})
const boundTemplate = computed(() => boundTemplateId.value ? templateStore.getTemplate(boundTemplateId.value) : null)
const templateBase  = computed(() => isSessionMode.value ? (boundTemplate.value?.config || {}) : {})

const draft = computed(() => settingsStore.getDraft(areaKey.value))

// Bound profile: from template profile_ids (session & template mode), not used in profile mode
const boundProfileId = computed(() => {
  if (isSessionMode.value)  return boundTemplate.value?.profile_ids?.[PROFILE_AREA] || null
  if (!isTemplateMode.value) return null
  return entity.value?.profile_ids?.[PROFILE_AREA] || null
})
const boundProfile = computed(() => boundProfileId.value ? profileStore.getProfile(boundProfileId.value) : null)

const profileBase = computed(() => {
  if (isSessionMode.value || isTemplateMode.value) return boundProfile.value?.config || {}
  return {}
})

const _TIER_FIELDS = [
  'provider_haiku_catalog_id', 'provider_haiku_model_id',
  'provider_sonnet_catalog_id', 'provider_sonnet_model_id',
  'provider_opus_catalog_id', 'provider_opus_model_id',
  'provider_default_tier',
]

function _allTierFieldsSet(cfg) {
  return _TIER_FIELDS.every(f => Boolean(cfg[f]))
}

const mergedConfig = computed(() => {
  const draftEntries = Object.entries(draft.value || {})
  const resetKeys = new Set(draftEntries.filter(([, v]) => v === FIELD_RESET).map(([k]) => k))
  const cleanDraft = Object.fromEntries(draftEntries.filter(([, v]) => v !== FIELD_RESET))
  const cleanBase = Object.fromEntries(Object.entries(baseConfig.value || {}).filter(([k]) => !resetKeys.has(k)))
  const merged = { ...profileBase.value, ...templateBase.value, ...cleanBase, ...cleanDraft }
  // Derive pseudo-field from actual tier fields unless the draft has explicitly set it
  if (!('provider_tier_routing_enabled' in cleanDraft)) {
    merged.provider_tier_routing_enabled = _allTierFieldsSet(merged)
  }
  return merged
})
const isDirty = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving  = ref(false)

const { fieldStates } = useEditSectionFieldStates({
  isTemplateMode, isSessionMode, isScheduleMode, baseConfig, draft,
  boundProfile, boundTemplate,
  schemaFields: FIELD_SCHEMAS.model,
})
const { handleReset } = useEditSectionReset({ areaKey })

const saveScheduleSessionConfig = useScheduleSectionSave({
  scheduleId: entityId,
  legionId: computed(() => entity.value?.legion_id),
  ephemeralAgentId: computed(() => entity.value?.ephemeral_agent_id),
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

function handleLinkedField(linked) {
  settingsStore.setField(areaKey.value, linked.key, linked.value)
}

const tierValidationError = ref(null)

async function handleSave() {
  if (!entity.value || !isDirty.value) return
  saving.value = true
  tierValidationError.value = null
  try {
    const d = { ...draft.value }
    const keysToDelete = Object.keys(d).filter(k => d[k] === FIELD_RESET)
    for (const k of keysToDelete) delete d[k]

    // Handle provider_tier_routing_enabled pseudo-field (never persisted)
    const tierEnabled = mergedConfig.value.provider_tier_routing_enabled
    delete d.provider_tier_routing_enabled
    if (tierEnabled) {
      const current = { ...(entity.value?.config || entity.value?.session_config || {}), ...d }
      if (_TIER_FIELDS.some(f => !current[f])) {
        tierValidationError.value = 'All 3 tier rows must be configured and a default tier selected.'
        return
      }
    } else {
      // Tier toggle off: clear all 7 tier fields from saved config
      for (const f of _TIER_FIELDS) {
        d[f] = null
        keysToDelete.push(f)
      }
    }

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
    } else if (isTemplateMode.value) {
      if (keysToDelete.length > 0) {
        const newConfig = { ...(entity.value?.config || {}), ...d }
        for (const k of keysToDelete) delete newConfig[k]
        await templateStore.updateTemplate(entityId.value, { config: newConfig })
      } else {
        await templateStore.updateTemplate(entityId.value, d)
      }
    } else {
      const newConfig = { ...(entity.value?.config || {}), ...d }
      for (const k of keysToDelete) delete newConfig[k]
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
  if (isScheduleMode.value) scheduleStore.loadAllSchedules()
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
