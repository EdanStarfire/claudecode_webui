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
import FieldSection from '../../configuration/fields/FieldSection.vue'
import { FIELD_SCHEMAS } from '../../configuration/fields/fieldSchemas.js'
import { useEditSectionFieldStates } from '@/composables/useEditSectionFieldStates'

const PROFILE_AREA = 'isolation'
const SECTION_KEY  = 'isolation'

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

const draft       = computed(() => settingsStore.getDraft(areaKey.value))
const profileBase = computed(() => isTemplateMode.value && boundProfile.value?.config ? boundProfile.value.config : {})
const mergedConfig = computed(() => {
  const c = { ...profileBase.value, ...baseConfig.value, ...draft.value }
  // docker_extra_mounts is stored as array in API; TextareaWidget expects newline-separated string
  if (Array.isArray(c.docker_extra_mounts)) c.docker_extra_mounts = c.docker_extra_mounts.join('\n')
  return c
})
const isDirty      = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving       = ref(false)

const isolationFields = computed(() => FIELD_SCHEMAS.isolation.map(f => {
  if (f.key === 'docker_proxy_enabled') return { ...f, description: 'Intercepts outbound traffic via a dedicated proxy sidecar. Requires <code>claude-proxy:local</code> image.' }
  if (f.key === 'docker_extra_mounts')  return { ...f, placeholder: '{session_data}/db:/app/db:ro or /host/path:/container/path:ro (one per line)', description: 'Supports template variables: {session_id}, {session_data}, {working_dir}.' }
  if (f.key === 'bare_mode')            return { ...f, description: 'Skips hooks, LSP, plugin sync, and skill directory walks. Requires ANTHROPIC_API_KEY.' }
  if (f.key === 'env_scrub_enabled')    return { ...f, description: 'Strips Anthropic API keys and cloud provider credentials from subprocess environments.' }
  return f
}))

const boundProfileId = computed(() => {
  if (!isTemplateMode.value) return null
  return entity.value?.profile_ids?.[PROFILE_AREA] || null
})
const boundProfile = computed(() => boundProfileId.value ? profileStore.getProfile(boundProfileId.value) : null)

const { fieldStates } = useEditSectionFieldStates({ isTemplateMode, baseConfig, draft, boundProfile, schemaFields: FIELD_SCHEMAS.isolation })

const toolbarChips = computed(() => {
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
    // sandbox_config is a nested object; merge with existing
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
    // Convert textarea string back to array for the API
    if ('docker_extra_mounts' in d && typeof d.docker_extra_mounts === 'string') {
      d.docker_extra_mounts = d.docker_extra_mounts.split('\n').map(s => s.trim()).filter(Boolean)
    }
    if (isTemplateMode.value) {
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
