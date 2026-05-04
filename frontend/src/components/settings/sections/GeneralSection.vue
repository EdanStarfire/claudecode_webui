<template>
  <div class="settings-section">
    <SettingsToolbar
      title="General"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    />

    <div v-if="!entity && !isNew" class="section-loading">Loading…</div>

    <div v-else class="section-body">
      <!-- Name -->
      <div class="field-row">
        <label class="field-label">{{ isTemplateMode ? 'Template Name' : 'Profile Name' }}</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :value="mergedConfig.name"
            placeholder="Name"
            @input="handleField('name', $event.target.value)"
          />
        </div>
      </div>

      <!-- Description (template only) -->
      <div v-if="isTemplateMode" class="field-row">
        <label class="field-label">Description</label>
        <div class="field-control">
          <textarea
            class="field-input field-textarea"
            :value="mergedConfig.description"
            rows="2"
            placeholder="Brief description of this template's purpose"
            @input="handleField('description', $event.target.value)"
          />
        </div>
      </div>

      <!-- Area (profile only, read-only) -->
      <div v-if="isProfileMode" class="field-row">
        <label class="field-label">Area</label>
        <div class="field-control">
          <span class="field-value-readonly">{{ isNew ? newArea : entity?.area }}</span>
        </div>
      </div>

      <!-- Role (template only) -->
      <div v-if="isTemplateMode" class="field-row">
        <label class="field-label">Role</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :value="mergedConfig.role"
            placeholder="e.g., Code review specialist"
            @input="handleField('role', $event.target.value)"
          />
        </div>
      </div>

      <!-- Profile bindings per area (template only) -->
      <div v-if="isTemplateMode" class="field-row field-row--bindings">
        <label class="field-label field-label--top">Profile Bindings</label>
        <div class="field-control">
          <div class="bindings-help">Assign a profile to each configuration area for inheritance.</div>
          <div class="profile-bindings">
            <div
              v-for="area in PROFILE_AREAS"
              :key="area.key"
              class="binding-row"
            >
              <label class="binding-area-label">{{ area.label }}</label>
              <select
                class="binding-select"
                :value="(mergedConfig.profile_ids || {})[area.key] || ''"
                @change="handleProfileBinding(area.key, $event.target.value || null)"
              >
                <option value="">(no profile)</option>
                <option
                  v-for="p in profileStore.profilesForArea(area.key)"
                  :key="p.profile_id"
                  :value="p.profile_id"
                >{{ p.name }}</option>
              </select>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useTemplateStore } from '@/stores/template'
import { useProfileStore } from '@/stores/profile'
import SettingsToolbar from '../SettingsToolbar.vue'

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()
const templateStore = useTemplateStore()
const profileStore = useProfileStore()

const isTemplateMode = computed(() => route.path.startsWith('/settings/template/'))
const isProfileMode  = computed(() => route.path.startsWith('/settings/profile/'))
const entityId       = computed(() => route.params.templateId || route.params.profileId || '')
const areaKey        = computed(() => {
  const prefix = isTemplateMode.value ? 'template' : 'profile'
  return `${prefix}:${entityId.value}:general`
})

const isNew  = computed(() => entityId.value === '__new__')
const newArea = computed(() => route.query.area || 'model')

const entity = computed(() => {
  if (isNew.value) return null
  if (isTemplateMode.value) return templateStore.getTemplate(entityId.value)
  if (isProfileMode.value)  return profileStore.getProfile(entityId.value)
  return null
})

const draft = computed(() => settingsStore.getDraft(areaKey.value))

const mergedConfig = computed(() => ({
  ...(entity.value || {}),
  // profile_ids needs deep merge to avoid overwriting all at once
  profile_ids: {
    ...((entity.value?.profile_ids) || {}),
    ...((draft.value?.profile_ids) || {}),
  },
  ...draft.value,
}))

const isDirty = computed(() => isNew.value || settingsStore.dirtyAreas.has(areaKey.value))
const saving  = ref(false)

const PROFILE_AREAS = [
  { key: 'model',         label: 'Model' },
  { key: 'permissions',   label: 'Permissions' },
  { key: 'system_prompt', label: 'System Prompt' },
  { key: 'mcp',           label: 'MCP Servers' },
  { key: 'isolation',     label: 'Isolation' },
  { key: 'features',      label: 'Features' },
]

function handleField(key, value) {
  settingsStore.setField(areaKey.value, key, value)
}

function handleProfileBinding(area, profileId) {
  const current = { ...((entity.value?.profile_ids) || {}), ...((draft.value?.profile_ids) || {}) }
  const updated = { ...current, [area]: profileId }
  settingsStore.setField(areaKey.value, 'profile_ids', updated)
}

async function handleSave() {
  saving.value = true
  try {
    if (isNew.value) {
      const name = (draft.value?.name || '').trim() || (isTemplateMode.value ? 'New Template' : 'New Profile')
      if (isTemplateMode.value) {
        const result = await templateStore.createTemplate({ name, config: {} })
        settingsStore.discardDraft(areaKey.value)
        router.replace(`/settings/template/${result.template_id}/general`)
      } else {
        const profile = await profileStore.createProfile(name, newArea.value, {})
        settingsStore.discardDraft(areaKey.value)
        router.replace(`/settings/profile/${profile.profile_id}/general`)
      }
      return
    }
    if (!entity.value || !isDirty.value) return
    const d = { ...draft.value }
    if (isTemplateMode.value) {
      await templateStore.updateTemplate(entityId.value, d)
    } else {
      await profileStore.updateProfile(entityId.value, { name: d.name })
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
  if (isNew.value) {
    if (isTemplateMode.value) router.push('/settings/templates')
    else router.push('/settings/profiles')
  }
}

defineExpose({ save: handleSave })

onMounted(() => {
  if (isTemplateMode.value) {
    templateStore.fetchTemplates()
    profileStore.fetchIfEmpty()
  }
  if (isProfileMode.value) {
    profileStore.fetchIfEmpty()
  }
})
</script>

<style scoped>
.settings-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-loading {
  padding: 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* Same grid as FieldRenderer: label (220px) | control (1fr) */
.field-row {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 12px;
  align-items: start;
  padding: 10px 0;
  border-bottom: 1px solid var(--bs-border-color);
}

.field-label {
  display: flex;
  align-items: center;
  padding-top: 5px;
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
}

.field-label--top {
  align-items: flex-start;
}

.field-control {
  min-width: 0;
}

.field-input {
  background: var(--bs-body-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 13px;
  color: var(--bs-body-color);
  width: 100%;
  transition: border-color 0.12s;
}

.field-input:focus {
  outline: none;
  border-color: #6366f1;
}

.field-textarea {
  resize: vertical;
  min-height: 56px;
}

.field-value-readonly {
  font-size: 13px;
  color: var(--bs-emphasis-color);
  padding-top: 5px;
  font-weight: 500;
  text-transform: capitalize;
}

.bindings-help {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  margin-bottom: 8px;
}

.profile-bindings {
  border: 1px solid var(--bs-border-color);
  border-radius: 8px;
  overflow: hidden;
}

.binding-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--bs-border-color);
}

.binding-row:last-child {
  border-bottom: none;
}

.binding-area-label {
  font-size: 13px;
  color: var(--bs-body-color);
  min-width: 100px;
}

.binding-select {
  flex: 1;
  background: var(--bs-body-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 5px;
  padding: 4px 8px;
  font-size: 12px;
  color: var(--bs-body-color);
}

.binding-select:focus {
  outline: none;
  border-color: #6366f1;
}

@container settings-area (max-width: 599px) {
  .field-row {
    grid-template-columns: 1fr;
  }

  .field-label {
    padding-top: 0;
  }
}
</style>
