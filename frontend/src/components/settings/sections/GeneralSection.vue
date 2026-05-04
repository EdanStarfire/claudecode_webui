<template>
  <div class="settings-section">
    <SettingsToolbar
      :title="isSessionMode ? 'General' : 'General'"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    >
      <!-- Session mode: editable T: chip (template selector dropdown) -->
      <template v-if="isSessionMode" #chips-extra>
        <div class="chip-t template-chip-select" :title="templateChipTooltip">
          <span class="chip-badge">T:</span>
          <select class="chip-select-input" :value="currentTemplateId" @change="handleTemplateChange">
            <option value="">None</option>
            <option
              v-for="t in templateStore.templateList"
              :key="t.template_id"
              :value="t.template_id"
            >{{ t.name }}</option>
            <option v-if="!templateStore.templateList.length" value="__create__">Create new →</option>
          </select>
          <span v-if="templateStore.templateList.length === 0" class="chip-no-templates">
            <router-link to="/settings/templates" class="chip-create-link">Create new →</router-link>
          </span>
        </div>
      </template>
    </SettingsToolbar>

    <div v-if="!entity && !isNew" class="section-loading">Loading…</div>

    <div v-else class="section-body">

      <!-- Session mode fields -->
      <template v-if="isSessionMode">
        <!-- Session Name -->
        <div class="field-row">
          <label class="field-label">Session Name</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input"
              :value="currentName"
              placeholder="Session name"
              @input="handleField('name', $event.target.value)"
            />
          </div>
        </div>

        <!-- Working Directory -->
        <div class="field-row">
          <label class="field-label">Working Directory</label>
          <div class="field-control">
            <!-- New session: editable with folder browser -->
            <div v-if="isNew" class="dir-input-row">
              <input
                type="text"
                class="field-input"
                :value="currentWorkingDir"
                :placeholder="projectWorkingDir || '/path/to/project'"
                @input="handleField('working_directory', $event.target.value)"
              />
              <button class="btn-browse" title="Browse…" @click="openWorkingDirBrowser">📁</button>
            </div>
            <!-- Existing session: read-only -->
            <span v-else class="field-value-readonly field-value-mono">{{ currentWorkingDir }}</span>
            <div class="field-helper">The session's working directory (cannot be changed after creation)</div>
          </div>
        </div>

        <!-- Role -->
        <div class="field-row">
          <label class="field-label">Role</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input"
              :value="currentRole"
              placeholder="e.g., Code review specialist"
              @input="handleField('role', $event.target.value)"
            />
          </div>
        </div>

        <!-- Capabilities -->
        <div class="field-row">
          <label class="field-label">Capabilities</label>
          <div class="field-control">
            <TagInputWidget
              :value="currentCapabilities"
              variant="capability"
              placeholder="Add capability…"
              :default-value="null"
              @update:value="handleField('capabilities', $event)"
            />
          </div>
        </div>

        <!-- Bound template info (read-only metadata) -->
        <div v-if="boundTemplate" class="field-row">
          <label class="field-label">Template</label>
          <div class="field-control">
            <span class="field-value-readonly">{{ boundTemplate.name }}</span>
            <div v-if="boundTemplate.description" class="field-description">{{ boundTemplate.description }}</div>
          </div>
        </div>
        <div v-else class="field-row">
          <label class="field-label">Template</label>
          <div class="field-control">
            <span class="field-value-muted">No template bound — fields show default values only</span>
          </div>
        </div>

        <!-- Profile bindings inherited from bound template (read-only) -->
        <div v-if="boundTemplate" class="field-row field-row--bindings">
          <label class="field-label field-label--top">Profile Bindings</label>
          <div class="field-control">
            <div class="bindings-help">Profiles inherited from the bound template.</div>
            <div class="profile-bindings">
              <div v-for="area in PROFILE_AREAS" :key="area.key" class="binding-row">
                <label class="binding-area-label">{{ area.label }}</label>
                <button
                  v-if="templateProfileFor(area.key)"
                  class="profile-link-chip"
                  :title="`Edit ${templateProfileFor(area.key).name}`"
                  @click="goToProfile(area.key)"
                >
                  {{ templateProfileFor(area.key).name }}
                </button>
                <span v-else class="profile-none">—</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- Template mode fields -->
      <template v-else-if="isTemplateMode">
        <div class="field-row">
          <label class="field-label">Template Name</label>
          <div class="field-control">
            <input type="text" class="field-input" :value="mergedConfig.name" placeholder="Name" @input="handleField('name', $event.target.value)" />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Description</label>
          <div class="field-control">
            <textarea class="field-input field-textarea" :value="mergedConfig.description" rows="2" placeholder="Brief description of this template's purpose" @input="handleField('description', $event.target.value)" />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Role</label>
          <div class="field-control">
            <input type="text" class="field-input" :value="mergedConfig.role" placeholder="e.g., Code review specialist" @input="handleField('role', $event.target.value)" />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Capabilities</label>
          <div class="field-control">
            <TagInputWidget
              :value="mergedConfig.capabilities"
              variant="capability"
              placeholder="Add capability..."
              :default-value="null"
              @update:value="handleField('capabilities', $event)"
            />
          </div>
        </div>
        <div class="field-row field-row--bindings">
          <label class="field-label field-label--top">Profile Bindings</label>
          <div class="field-control">
            <div class="bindings-help">Assign a profile to each configuration area for inheritance.</div>
            <div class="profile-bindings">
              <div v-for="area in PROFILE_AREAS" :key="area.key" class="binding-row">
                <label class="binding-area-label">{{ area.label }}</label>
                <select class="binding-select" :value="(mergedConfig.profile_ids || {})[area.key] || ''" @change="handleProfileBinding(area.key, $event.target.value || null)">
                  <option value="">(no profile)</option>
                  <option v-for="p in profileStore.profilesForArea(area.key)" :key="p.profile_id" :value="p.profile_id">{{ p.name }}</option>
                </select>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- Profile mode fields -->
      <template v-else-if="isProfileMode">
        <div class="field-row">
          <label class="field-label">Profile Name</label>
          <div class="field-control">
            <input type="text" class="field-input" :value="mergedConfig.name" placeholder="Name" @input="handleField('name', $event.target.value)" />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Area</label>
          <div class="field-control">
            <span class="field-value-readonly">{{ isNew ? newArea : entity?.area }}</span>
          </div>
        </div>
      </template>

    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useTemplateStore } from '@/stores/template'
import { useProfileStore } from '@/stores/profile'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useProjectStore } from '@/stores/project'
import SettingsToolbar from '../SettingsToolbar.vue'
import TagInputWidget from '../../configuration/fields/TagInputWidget.vue'

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()
const templateStore = useTemplateStore()
const profileStore = useProfileStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const projectStore = useProjectStore()

const isTemplateMode = computed(() => route.path.startsWith('/settings/template/'))
const isProfileMode  = computed(() => route.path.startsWith('/settings/profile/'))
const isSessionMode  = computed(() => route.path.startsWith('/settings/session/'))
const entityId       = computed(() => route.params.sessionId || route.params.templateId || route.params.profileId || '')
const areaKey        = computed(() => {
  if (isSessionMode.value) return `session:${entityId.value}:general`
  const prefix = isTemplateMode.value ? 'template' : 'profile'
  return `${prefix}:${entityId.value}:general`
})

const isNew   = computed(() => entityId.value === '__new__')
const newArea = computed(() => route.query.area || 'model')

const entity = computed(() => {
  if (isNew.value) return null
  if (isSessionMode.value)  return sessionStore.getSession(entityId.value)
  if (isTemplateMode.value) return templateStore.getTemplate(entityId.value)
  if (isProfileMode.value)  return profileStore.getProfile(entityId.value)
  return null
})

const draft = computed(() => settingsStore.getDraft(areaKey.value))

// Session mode: current template_id (from draft or entity)
const currentTemplateId = computed(() => {
  if (!isSessionMode.value) return ''
  if (draft.value && 'template_id' in draft.value) return draft.value.template_id || ''
  return entity.value?.template_id || ''
})

// Session mode: bound template object
const boundTemplate = computed(() => {
  if (!isSessionMode.value) return null
  const tid = currentTemplateId.value
  return tid ? templateStore.getTemplate(tid) : null
})

const templateChipTooltip = computed(() => {
  if (!isSessionMode.value) return ''
  return currentTemplateId.value
    ? `Template: ${boundTemplate.value?.name || currentTemplateId.value}`
    : 'No template bound'
})

// Session mode: current name (from draft or entity)
const currentName = computed(() => {
  if (!isSessionMode.value) return ''
  if (draft.value && 'name' in draft.value) return draft.value.name
  return entity.value?.name || ''
})

// For new sessions: default working directory comes from the project
const projectWorkingDir = computed(() => {
  if (!isSessionMode.value || !isNew.value) return ''
  const pid = route.query.project_id
  return pid ? (projectStore.getProject(pid)?.working_directory || '') : ''
})

const currentWorkingDir = computed(() => {
  if (!isSessionMode.value) return ''
  if (draft.value && 'working_directory' in draft.value) return draft.value.working_directory || ''
  // Existing session: always has working_directory set by backend
  if (!isNew.value) return entity.value?.working_directory || ''
  // New session: default to project's working directory
  return projectWorkingDir.value
})

const currentRole = computed(() => {
  if (!isSessionMode.value) return ''
  if (draft.value && 'role' in draft.value) return draft.value.role || ''
  return entity.value?.role || ''
})

const currentCapabilities = computed(() => {
  if (!isSessionMode.value) return null
  if (draft.value && 'capabilities' in draft.value) return draft.value.capabilities
  const arr = entity.value?.capabilities
  if (!arr || arr.length === 0) return null
  return Array.isArray(arr) ? arr.join(', ') : arr
})

// Template/profile mode: mergedConfig
const capabilitiesStr = computed(() => {
  if (draft.value && 'capabilities' in draft.value) return draft.value.capabilities
  const arr = entity.value?.capabilities
  if (!arr || arr.length === 0) return null
  return Array.isArray(arr) ? arr.join(', ') : arr
})

const mergedConfig = computed(() => ({
  ...(entity.value || {}),
  profile_ids: {
    ...((entity.value?.profile_ids) || {}),
    ...((draft.value?.profile_ids) || {}),
  },
  ...draft.value,
  capabilities: capabilitiesStr.value,
}))

const isDirty = computed(() => {
  // New-session mode always shows Save (no entity yet to compare against)
  if (isSessionMode.value && isNew.value) return true
  if (isSessionMode.value) return settingsStore.dirtyAreas.has(areaKey.value)
  return isNew.value || settingsStore.dirtyAreas.has(areaKey.value)
})
const saving = ref(false)

const PROFILE_AREAS = [
  { key: 'model',         label: 'Model' },
  { key: 'permissions',   label: 'Permissions' },
  { key: 'system_prompt', label: 'System Prompt' },
  { key: 'mcp',           label: 'MCP Servers' },
  { key: 'isolation',     label: 'Isolation' },
  { key: 'features',      label: 'Features' },
]

const AREA_TO_SECTION = {
  model:         'model-tuning',
  permissions:   'tools-permissions',
  mcp:           'mcp-servers',
  features:      'features',
  system_prompt: 'system-prompt',
  isolation:     'isolation',
}

function templateProfileFor(areaKey) {
  const profileId = boundTemplate.value?.profile_ids?.[areaKey]
  return profileId ? profileStore.getProfile(profileId) : null
}

function goToProfile(areaKey) {
  const profileId = boundTemplate.value?.profile_ids?.[areaKey]
  if (!profileId) return
  const section = AREA_TO_SECTION[areaKey] || 'general'
  router.push(`/settings/profile/${profileId}/${section}`)
}

function handleField(key, value) {
  settingsStore.setField(areaKey.value, key, value)
}

function openWorkingDirBrowser() {
  uiStore.showModal('folder-browser', {
    currentPath: currentWorkingDir.value || '',
    onSelect: (path) => handleField('working_directory', path),
  })
}

function handleTemplateChange(event) {
  const value = event.target.value
  if (value === '__create__') {
    router.push('/settings/templates')
    return
  }
  settingsStore.setField(areaKey.value, 'template_id', value || null)
}

function handleProfileBinding(area, profileId) {
  const current = { ...((entity.value?.profile_ids) || {}), ...((draft.value?.profile_ids) || {}) }
  if (profileId) {
    current[area] = profileId
  } else {
    delete current[area]
  }
  settingsStore.setField(areaKey.value, 'profile_ids', current)
}

async function handleSave() {
  saving.value = true
  try {
    if (isSessionMode.value && isNew.value) {
      // Create a brand-new session — only name + template_id + identity fields, no config defaults
      const projectId = route.query.project_id
      if (!projectId) return
      const name = (draft.value?.name || '').trim() || 'New Session'
      const templateId = draft.value?.template_id || undefined
      // Use draft value, or fall back to project directory (backend also resolves this, but
      // sending it explicitly ensures it is stored in session.working_directory on first save)
      const workingDir = (draft.value?.working_directory?.trim() || projectWorkingDir.value) || undefined
      const role = draft.value?.role?.trim() || undefined
      let caps = draft.value?.capabilities
      if (typeof caps === 'string') caps = caps.split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
      const session = await sessionStore.createSession(projectId, {
        name,
        ...(templateId ? { template_id: templateId } : {}),
        ...(workingDir ? { working_directory: workingDir } : {}),
        ...(role ? { role } : {}),
        ...(caps?.length ? { capabilities: caps } : {}),
      })
      settingsStore.discardDraft(areaKey.value)
      await router.replace(`/settings/session/${session.session_id}/general`)
      return
    }
    if (isSessionMode.value) {
      if (!entity.value || !isDirty.value) return
      const d = { ...draft.value }
      const payload = {}
      if ('name' in d) payload.name = d.name || null
      if ('template_id' in d) payload.template_id = d.template_id || null
      // working_directory is immutable after creation — never PATCH it
      if ('role' in d) payload.role = d.role || null
      if ('capabilities' in d) {
        let caps = d.capabilities
        if (typeof caps === 'string') caps = caps.split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
        payload.capabilities = caps || []
      }
      await sessionStore.patchSession(entityId.value, payload)
      settingsStore.markClean(areaKey.value)
      return
    }
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
      if ('capabilities' in d && typeof d.capabilities === 'string') {
        d.capabilities = d.capabilities.split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
      }
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
    if (isSessionMode.value) router.push('/')
    else if (isTemplateMode.value) router.push('/settings/templates')
    else router.push('/settings/profiles')
  }
}

defineExpose({ save: handleSave, cancel: handleCancel })

onMounted(() => {
  if (isSessionMode.value) {
    templateStore.fetchTemplates()
    profileStore.fetchIfEmpty()
  }
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

.field-value-muted {
  font-size: 12px;
  color: var(--bs-tertiary-color);
  padding-top: 6px;
  font-style: italic;
}

.field-description {
  font-size: 11px;
  color: var(--bs-secondary-color);
  margin-top: 4px;
  line-height: 1.4;
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

.profile-link-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid var(--p-border, rgba(63, 185, 80, 0.30));
  background: var(--p-tint, rgba(63, 185, 80, 0.10));
  color: var(--p-fg, #3fb950);
  cursor: pointer;
  transition: opacity 0.12s;
}

.profile-link-chip:hover {
  opacity: 0.8;
}

.profile-none {
  font-size: 13px;
  color: var(--bs-tertiary-color);
  padding: 3px 0;
}

/* Editable T: chip for template selector in session General toolbar */
.template-chip-select {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 6px 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid rgba(31, 111, 235, 0.27);
  background: rgba(31, 111, 235, 0.1);
  color: #58a6ff;
  cursor: pointer;
}

.chip-badge {
  font-weight: 700;
  opacity: 0.75;
}

.chip-select-input {
  background: transparent;
  border: none;
  outline: none;
  color: inherit;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  padding: 0 2px;
  appearance: auto;
  max-width: 140px;
}

.chip-no-templates {
  font-size: 11px;
}

.chip-create-link {
  color: #58a6ff;
  text-decoration: none;
  font-size: 11px;
}

.chip-create-link:hover {
  text-decoration: underline;
}

.field-helper {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  margin-top: 5px;
  line-height: 1.4;
}

.field-value-mono {
  font-family: var(--bs-font-monospace);
  font-size: 12px;
  word-break: break-all;
}

.dir-input-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.dir-input-row .field-input {
  flex: 1;
}

.btn-browse {
  flex-shrink: 0;
  padding: 5px 9px;
  background: var(--bs-tertiary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.12s;
}

.btn-browse:hover {
  background: var(--bs-secondary-bg);
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
