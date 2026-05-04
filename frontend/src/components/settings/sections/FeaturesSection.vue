<template>
  <div class="settings-section">
    <SettingsToolbar
      title="Features"
      :chips="toolbarChips"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    />

    <div v-if="!entity" class="section-loading">Loading…</div>

    <div v-else-if="isNotApplicable" class="section-na">
      <p>Features fields are not applicable for a <strong>{{ entity.area }}</strong> profile.</p>
    </div>

    <div v-else class="section-body">
      <FieldSection
        :fields="featuresFields"
        :config="mergedConfig"
        :show-badges="true"
        :field-states="fieldStates"
        :show-include-toggle="false"
        @update:config="handleField"
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

const PROFILE_AREA = 'features'
const SECTION_KEY  = 'features'

const route = useRoute()
const settingsStore = useSettingsStore()
const templateStore = useTemplateStore()
const profileStore  = useProfileStore()
const sessionStore  = useSessionStore()

const isTemplateMode = computed(() => route.path.startsWith('/settings/template/'))
const isProfileMode  = computed(() => route.path.startsWith('/settings/profile/'))
const isSessionMode  = computed(() => route.path.startsWith('/settings/session/'))
const entityId       = computed(() => route.params.sessionId || route.params.templateId || route.params.profileId || '')
const areaKey        = computed(() => {
  if (isSessionMode.value) return `session:${entityId.value}:${SECTION_KEY}`
  const prefix = isTemplateMode.value ? 'template' : 'profile'
  return `${prefix}:${entityId.value}:${SECTION_KEY}`
})

const entity = computed(() => {
  if (isSessionMode.value)  return sessionStore.getSession(entityId.value)
  if (isTemplateMode.value) return templateStore.getTemplate(entityId.value)
  if (isProfileMode.value)  return profileStore.getProfile(entityId.value)
  return null
})

const isNotApplicable = computed(() =>
  !isSessionMode.value && isProfileMode.value && entity.value?.area !== PROFILE_AREA
)

const baseConfig = computed(() => entity.value?.config || {})

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
  return { ...profileBase.value, ...templateBase.value, ...cleanBase, ...cleanDraft }
})
const isDirty = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving  = ref(false)

const featuresFields = computed(() => FIELD_SCHEMAS.features.map(f => {
  if (f.key === 'auto_memory_mode')              return { ...f, description: 'Claude: built-in working-directory memory. Session-Specific: per-session guidance file. Disabled: no auto-memory.' }
  if (f.key === 'auto_memory_directory')         return { ...f, description: 'Custom directory for auto-memory storage. Supports template variables: {session_id}, {session_data}, {working_dir}.' }
  if (f.key === 'skill_creating_enabled')        return { ...f, description: "When enabled, the session's system prompt includes guidance on creating custom local skills." }
  if (f.key === 'history_distillation_enabled')  return { ...f, description: 'When enabled, session history is distilled to markdown on archive for context continuity.' }
  return f
}))

const { fieldStates } = useEditSectionFieldStates({
  isTemplateMode, isSessionMode, baseConfig, draft,
  boundProfile, boundTemplate,
  schemaFields: FIELD_SCHEMAS.features,
})
const { handleReset } = useEditSectionReset({ areaKey })

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

async function handleSave() {
  if (!entity.value || !isDirty.value) return
  saving.value = true
  try {
    const d = { ...draft.value }
    const keysToDelete = Object.keys(d).filter(k => d[k] === FIELD_RESET)
    for (const k of keysToDelete) delete d[k]
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
