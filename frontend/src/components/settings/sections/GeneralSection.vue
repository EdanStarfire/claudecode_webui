<template>
  <div class="settings-section">
    <SettingsToolbar
      title="General"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    />

    <div v-if="!entity" class="section-loading">Loading…</div>

    <div v-else class="section-body">
      <!-- Name -->
      <div class="field-row">
        <label class="field-label">{{ isTemplateMode ? 'Template Name' : 'Profile Name' }}</label>
        <input
          type="text"
          class="field-input"
          :value="mergedConfig.name"
          placeholder="Name"
          @input="handleField('name', $event.target.value)"
        />
      </div>

      <!-- Description (template only) -->
      <div v-if="isTemplateMode" class="field-row">
        <label class="field-label">Description</label>
        <textarea
          class="field-input field-textarea"
          :value="mergedConfig.description"
          rows="2"
          placeholder="Brief description of this template's purpose"
          @input="handleField('description', $event.target.value)"
        />
      </div>

      <!-- Area (profile only, read-only) -->
      <div v-if="isProfileMode" class="field-row">
        <label class="field-label">Area</label>
        <span class="field-value-readonly">{{ entity.area }}</span>
      </div>

      <!-- Role (template only) -->
      <div v-if="isTemplateMode" class="field-row">
        <label class="field-label">Role</label>
        <input
          type="text"
          class="field-input"
          :value="mergedConfig.role"
          placeholder="e.g., Code review specialist"
          @input="handleField('role', $event.target.value)"
        />
      </div>

      <!-- Permission Mode (template only) -->
      <div v-if="isTemplateMode" class="field-row">
        <label class="field-label">Permission Mode</label>
        <div class="perm-btn-group">
          <button
            v-for="opt in PERM_OPTIONS"
            :key="opt.value"
            type="button"
            class="perm-btn"
            :class="{ active: (mergedConfig.permission_mode || 'default') === opt.value }"
            @click="handleField('permission_mode', opt.value)"
          >{{ opt.label }}</button>
        </div>
      </div>

      <!-- Profile bindings per area (template only) -->
      <div v-if="isTemplateMode" class="profile-bindings">
        <div class="bindings-label">Profile Bindings</div>
        <div class="bindings-help">Assign a profile to each configuration area for inheritance.</div>
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
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useTemplateStore } from '@/stores/template'
import { useProfileStore } from '@/stores/profile'
import SettingsToolbar from '../SettingsToolbar.vue'

const route = useRoute()
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

const entity = computed(() => {
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

const isDirty = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving  = ref(false)

const PERM_OPTIONS = [
  { value: 'default',            label: 'Default' },
  { value: 'acceptEdits',        label: 'Accept Edits' },
  { value: 'plan',               label: 'Plan' },
  { value: 'dontAsk',            label: "Don't Ask" },
  { value: 'auto',               label: 'Auto' },
  { value: 'bypassPermissions',  label: 'Bypass' },
]

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
  if (!entity.value || !isDirty.value) return
  saving.value = true
  try {
    const d = { ...draft.value }
    if (isTemplateMode.value) {
      await templateStore.updateTemplate(entityId.value, d)
    } else {
      // Profile: only name is editable on general section
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
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  text-transform: uppercase;
  letter-spacing: 0.04em;
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
  padding: 6px 0;
  font-weight: 500;
  text-transform: capitalize;
}

/* Permission mode button group */
.perm-btn-group {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.perm-btn {
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.perm-btn:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

.perm-btn.active {
  background: rgba(99, 102, 241, 0.15);
  border-color: #6366f1;
  color: #6366f1;
}

/* Profile bindings */
.profile-bindings {
  border: 1px solid var(--bs-border-color);
  border-radius: 8px;
  overflow: hidden;
}

.bindings-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 10px 14px 4px;
}

.bindings-help {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  padding: 0 14px 8px;
}

.binding-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 14px;
  border-top: 1px solid var(--bs-border-color);
}

.binding-area-label {
  font-size: 13px;
  color: var(--bs-body-color);
  min-width: 120px;
}

.binding-select {
  flex: 1;
  max-width: 280px;
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
</style>
