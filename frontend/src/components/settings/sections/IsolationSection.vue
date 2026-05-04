<template>
  <div class="settings-section">
    <SettingsToolbar
      title="Isolation"
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
      <p>Isolation fields are not applicable for a <strong>{{ entity.area }}</strong> profile.</p>
    </div>

    <div v-else class="section-body">
      <FieldSection
        :fields="isolationFields"
        :config="mergedConfig"
        :show-badges="true"
        :field-states="fieldStates"
        :show-include-toggle="false"
        @update:config="handleIsolationField"
        @reset="handleReset"
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

const PROFILE_AREA = 'isolation'
const SECTION_KEY  = 'isolation'

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
  const c = { ...profileBase.value, ...templateBase.value, ...cleanBase, ...cleanDraft }
  if (Array.isArray(c.docker_extra_mounts)) c.docker_extra_mounts = c.docker_extra_mounts.join('\n')
  return c
})
const isDirty = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving  = ref(false)

const isolationFields = computed(() => FIELD_SCHEMAS.isolation.map(f => {
  if (f.key === 'docker_enabled' && isSessionMode.value && entity.value && entity.value.state !== 'created') {
    return { ...f, disabledWhen: () => true, description: 'Docker isolation cannot be changed after session creation.' }
  }
  if (f.key === 'docker_proxy_enabled') return { ...f, description: 'Intercepts outbound traffic via a dedicated proxy sidecar. Requires <code>claude-proxy:local</code> image.' }
  if (f.key === 'docker_extra_mounts')  return { ...f, placeholder: '{session_data}/db:/app/db:ro or /host/path:/container/path:ro (one per line)', description: 'Supports template variables: {session_id}, {session_data}, {working_dir}.' }
  if (f.key === 'bare_mode')            return { ...f, description: 'Skips hooks, LSP, plugin sync, and skill directory walks. Requires ANTHROPIC_API_KEY.' }
  if (f.key === 'env_scrub_enabled')    return { ...f, description: 'Strips Anthropic API keys and cloud provider credentials from subprocess environments.' }
  return f
}))

const { fieldStates } = useEditSectionFieldStates({
  isTemplateMode, isSessionMode, isScheduleMode, baseConfig, draft,
  boundProfile, boundTemplate,
  schemaFields: FIELD_SCHEMAS.isolation,
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

function handleIsolationField(key, value) {
  if (key === 'sandbox_config') {
    const existing = mergedConfig.value.sandbox_config || mergedConfig.value.sandbox || {}
    settingsStore.setField(areaKey.value, 'sandbox_config', { ...existing, ...value })
    return
  }
  if (key === 'docker_enabled' && value) {
    settingsStore.setField(areaKey.value, 'cli_path', '')
  }
  settingsStore.setField(areaKey.value, key, value)
}

async function handleSave() {
  if (!entity.value || !isDirty.value) return
  saving.value = true
  try {
    const d = { ...draft.value }
    const keysToDelete = Object.keys(d).filter(k => d[k] === FIELD_RESET)
    for (const k of keysToDelete) delete d[k]
    if ('docker_extra_mounts' in d && typeof d.docker_extra_mounts === 'string') {
      d.docker_extra_mounts = d.docker_extra_mounts.split('\n').map(s => s.trim()).filter(Boolean)
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
