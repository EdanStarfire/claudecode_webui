<template>
  <div class="settings-section">
    <SettingsToolbar
      title="Hooks"
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
      <p>Hooks configuration is not applicable for a <strong>{{ entity.area }}</strong> profile.</p>
    </div>

    <div v-else class="section-body">
      <div class="section-label">Attached Hooks</div>

      <div v-if="attachedIds.length === 0" class="text-muted small py-2">
        No hooks attached.
      </div>

      <div
        v-for="(id, i) in attachedIds"
        :key="id"
        class="hook-row"
        draggable="true"
        @dragstart="onDragStart($event, i)"
        @dragover.prevent
        @drop="onDrop(i)"
        @dragend="dragIndex = null"
      >
        <span class="grip" title="Drag to reorder">&#8942;&#8942;</span>
        <div class="hook-row-info">
          <div class="hook-row-name">
            {{ hookStore.getConfig(id)?.name || 'Unknown hook' }}
            <span v-if="hookStore.getConfig(id) && !hookStore.getConfig(id).enabled" class="text-muted small">(disabled globally)</span>
          </div>
          <div class="hook-row-meta small text-muted">
            {{ (hookStore.getConfig(id)?.hooks || []).length }} hook{{ (hookStore.getConfig(id)?.hooks || []).length === 1 ? '' : 's' }} &middot; applied {{ ordinal(i + 1) }}
          </div>
        </div>
        <SourceMarker
          v-if="hookIdFieldStates[id]?.kind"
          :kind="hookIdFieldStates[id].kind"
          :template-name="hookIdFieldStates[id].templateName ?? null"
          :profile-name="hookIdFieldStates[id].profileName ?? null"
        />
        <button
          v-if="hookIdFieldStates[id]?.resettable"
          type="button"
          class="field-reset-btn"
          title="Reset to inherited value"
          @click="handleHookReset(id)"
        >&#8617;</button>
        <button class="btn btn-sm btn-outline-danger" @click="detachHook(id)">Detach</button>
      </div>

      <div class="input-group input-group-sm attach-select mt-1">
        <select
          class="form-select form-select-sm"
          :disabled="attachableConfigs.length === 0"
          v-model="pendingAttachId"
          @change="handleAttachSelect"
        >
          <option value="">{{ attachableConfigs.length ? 'Attach hook…' : 'No hooks available' }}</option>
          <option v-for="config in attachableConfigs" :key="config.id" :value="config.id">
            {{ config.name }}{{ !config.enabled ? ' (disabled globally)' : '' }}
          </option>
        </select>
      </div>

      <div class="field-help small text-muted mt-2">
        Drag to reorder — this controls execution order among your own WebUI hooks when two
        entries share an event/matcher. WebUI hooks are always additive: they never replace or
        reorder disk-based <code>.claude/settings.json</code> hooks. To run only WebUI hooks with
        no disk hooks at all, use this session/template's existing <strong>Setting Sources</strong>
        control (unrelated to this feature) and set it to load none.
      </div>

      <div class="divider"></div>

      <div class="d-flex justify-content-between align-items-center mb-2">
        <div class="section-label mb-0">Injected settings.json Preview</div>
        <span class="small text-muted">What gets written to the session's temp settings file at startup</span>
      </div>
      <pre class="json-preview">{{ jsonPreviewText }}</pre>
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
import { useHookConfigStore } from '@/stores/hooks'
import SettingsToolbar from '../SettingsToolbar.vue'
import SourceMarker from '../SourceMarker.vue'
import { FIELD_RESET } from '@/composables/fieldResetSentinel.js'
import { useScheduleStore } from '@/stores/schedule'
import { useScheduleSectionSave } from '@/composables/useScheduleSectionSave'

const PROFILE_AREA = 'hooks'
const SECTION_KEY  = 'hooks'

const route = useRoute()
const settingsStore = useSettingsStore()
const templateStore = useTemplateStore()
const profileStore  = useProfileStore()
const sessionStore  = useSessionStore()
const scheduleStore = useScheduleStore()
const hookStore     = useHookConfigStore()

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

const saveScheduleSessionConfig = useScheduleSectionSave({
  scheduleId: entityId,
  legionId: computed(() => entity.value?.legion_id),
  ephemeralAgentId: computed(() => entity.value?.ephemeral_agent_id),
})

const attachedIds = computed(() => mergedConfig.value.hook_ids || [])

const hookIdFieldStates = computed(() => {
  const result = {}

  const draftVal = draft.value?.hook_ids
  const isDraftReset = draftVal === FIELD_RESET
  const draftIds = (!isDraftReset && Array.isArray(draftVal)) ? new Set(draftVal) : null
  const baseIds = new Set(baseConfig.value?.hook_ids || [])
  const profileIds = new Set(profileBase.value?.hook_ids || [])
  const templateIds = new Set(templateBase.value?.hook_ids || [])
  const selfKind = isSessionMode.value ? 'S' : (isTemplateMode.value ? 'T' : 'P')
  const mergedIds = new Set(mergedConfig.value.hook_ids || [])

  const managedHere = !!(
    draftIds ||
    (!isDraftReset && baseConfig.value && 'hook_ids' in baseConfig.value)
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

function handleHookReset(id) {
  // Scoped reset: match this one id's presence to what upstream (template/profile)
  // says, leaving every other attached id's override untouched — mirrors
  // McpServersSection.vue's handleServerReset(). Resetting the whole `hook_ids`
  // field here would silently discard unrelated session-level attachments too.
  const current = attachedIds.value.filter(x => x !== id)
  const upstreamHasIt = profileBase.value?.hook_ids?.includes(id) ||
    (isSessionMode.value && templateBase.value?.hook_ids?.includes(id))
  if (upstreamHasIt) {
    current.push(id)
  }
  handleField('hook_ids', current)
}

function detachHook(id) {
  handleField('hook_ids', attachedIds.value.filter(x => x !== id))
}

const dragIndex = ref(null)
function onDragStart(event, index) {
  // Firefox refuses to start a native drag operation unless dataTransfer.setData()
  // is called during dragstart — Chrome is lenient about this, Firefox is not.
  event.dataTransfer?.setData('text/plain', '')
  dragIndex.value = index
}
function onDrop(targetIndex) {
  if (dragIndex.value === null || dragIndex.value === targetIndex) return
  const list = [...attachedIds.value]
  const [moved] = list.splice(dragIndex.value, 1)
  list.splice(targetIndex, 0, moved)
  handleField('hook_ids', list)
  dragIndex.value = null
}

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}

// ── Attach dropdown ────────────────────────────────────────────────────────
const pendingAttachId = ref('')

const attachableConfigs = computed(() =>
  hookStore.configList().filter(c => !attachedIds.value.includes(c.id))
)

function handleAttachSelect() {
  if (!pendingAttachId.value) return
  handleField('hook_ids', [...attachedIds.value, pendingAttachId.value])
  pendingAttachId.value = ''
}

// ── JSON preview ───────────────────────────────────────────────────────────
function entryToHookConfig(entry) {
  if (entry.type === 'http') {
    if (!entry.url) return null
    const cfg = { type: 'http', url: entry.url }
    if (entry.timeout != null) cfg.timeout = entry.timeout
    if (entry.headers && Object.keys(entry.headers).length) cfg.headers = entry.headers
    if (entry.allowed_env_vars && entry.allowed_env_vars.length) cfg.allowedEnvVars = entry.allowed_env_vars
    return cfg
  }
  if (!entry.command) return null
  const cfg = { type: 'command', command: entry.command }
  if (entry.timeout != null) cfg.timeout = entry.timeout
  return cfg
}

const jsonPreview = computed(() => {
  const events = {}
  for (const id of attachedIds.value) {
    const config = hookStore.getConfig(id)
    if (!config || !config.enabled) continue
    for (const entry of (config.hooks || [])) {
      if (!entry.enabled || !(entry.events || []).length) continue
      const hookConfig = entryToHookConfig(entry)
      if (!hookConfig) continue
      for (const event of entry.events) {
        const rule = { hooks: [hookConfig] }
        if (entry.matcher) rule.matcher = entry.matcher
        events[event] = events[event] || []
        events[event].push(rule)
      }
    }
  }
  return Object.keys(events).length ? { hooks: events } : {}
})

const jsonPreviewText = computed(() => JSON.stringify(jsonPreview.value, null, 2))

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
  hookStore.fetchConfigs()
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

.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 8px;
}

.hook-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  margin-bottom: 6px;
  background: var(--bs-tertiary-bg);
}

.hook-row .grip {
  color: var(--bs-secondary-color);
  cursor: grab;
}

.hook-row-info {
  flex: 1;
  min-width: 0;
}

.hook-row-name {
  font-weight: 600;
  font-size: 13px;
}

.field-reset-btn {
  background: none;
  border: none;
  padding: 0;
  font-size: 0.85rem;
  color: #6c757d;
  cursor: pointer;
  line-height: 1;
}
.field-reset-btn:hover { color: #dc3545; }

.divider {
  height: 1px;
  background: var(--bs-border-color);
  margin: 16px 0;
}

.json-preview {
  background: var(--bs-tertiary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 7px;
  padding: 12px;
  font-family: var(--bs-font-monospace, monospace);
  font-size: 11.5px;
  color: var(--bs-secondary-color);
  white-space: pre-wrap;
  max-height: 420px;
  overflow-y: auto;
  line-height: 1.6;
  margin: 0;
}

.attach-select {
  max-width: 320px;
}
</style>
