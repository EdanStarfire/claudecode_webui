<template>
  <div class="settings-section">
    <SettingsToolbar
      title="Relay"
      :chips="toolbarChips"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    >
      <template v-if="isSessionMode && relayNotAttachedReason" #chips-extra>
        <button
          type="button"
          class="restart-chip"
          data-testid="restart-required-chip"
          :title="relayNotAttachedReason"
          @click="openRestart"
        >⟳ Restart required</button>
      </template>
    </SettingsToolbar>

    <div v-if="!entity" class="section-loading">Loading…</div>

    <div v-else-if="isPermanentScheduleNA" class="section-na">
      <p>This schedule is bound to a permanent session. Only General settings apply.</p>
    </div>

    <div v-else-if="isNotApplicable" class="section-na">
      <p>Relay fields are not applicable for a <strong>{{ entity.area }}</strong> profile.</p>
    </div>

    <div v-else class="section-body">
      <div v-if="saveError" class="save-error" role="alert" data-testid="relay-save-error">{{ saveError }}</div>
      <FieldSection
        :fields="relayFields"
        :config="mergedConfig"
        :show-badges="true"
        :field-states="fieldStates"
        :show-include-toggle="false"
        :errors="fieldErrors"
        @update:config="handleField"
        @reset="handleReset"
      />
      <p v-if="isSessionMode && relayNotAttachedReason" class="restart-note" data-testid="relay-not-attached-note">
        {{ relayNotAttachedReason }}
      </p>

      <div v-if="policyEditorRelayId" class="policy-block" data-testid="relay-policy-block">
        <RelayPolicyEditor
          v-if="isSessionMode"
          :key="`${entityId}:${policyEditorRelayId}`"
          mode="live"
          :session-id="entityId"
        />
        <RelayPolicyEditor
          v-else-if="policyModel"
          :key="policyEditorRelayId"
          mode="local"
          :relay-id="policyEditorRelayId"
          :policy-model="policyModel"
          :model-value="ownPolicies"
          :inherited="inheritedPolicies"
          @update:model-value="handleField('relay_channel_policies', $event)"
        />
      </div>

      <div v-if="isSessionMode" class="handle-block" data-testid="relay-handle-block">
        <FieldRenderer
          :field="HANDLE_FIELD"
          :value="handleDraft"
          :error="handleError"
          :show-badges="false"
          @update:value="handleDraft = $event"
        />
        <div class="handle-actions">
          <button
            type="button"
            class="btn btn-sm btn-outline-primary"
            :disabled="!handleDirty || handleSaving"
            data-testid="relay-handle-save"
            @click="saveHandle"
          >{{ handleSaving ? 'Saving…' : 'Save handle' }}</button>
          <small class="text-muted">Saved immediately, independent of the form's Save.</small>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useTemplateStore } from '@/stores/template'
import { useProfileStore } from '@/stores/profile'
import { useSessionStore } from '@/stores/session'
import SettingsToolbar from '../SettingsToolbar.vue'
import FieldSection from '../../configuration/fields/FieldSection.vue'
import FieldRenderer from '../../configuration/fields/FieldRenderer.vue'
import { useRelayStore } from '@/stores/relay'
import RelayPolicyEditor from '../../relay/RelayPolicyEditor.vue'
import { normalizePolicies } from '@/utils/relayPolicies'
import { useRestartRequired } from '@/composables/useRestartRequired'
import { FIELD_SCHEMAS } from '../../configuration/fields/fieldSchemas.js'
import { useEditSectionFieldStates } from '@/composables/useEditSectionFieldStates'
import { useEditSectionReset } from '@/composables/useEditSectionReset'
import { FIELD_RESET } from '@/composables/fieldResetSentinel.js'
import { useScheduleStore } from '@/stores/schedule'
import { useScheduleSectionSave } from '@/composables/useScheduleSectionSave'

const PROFILE_AREA = 'relay'
const SECTION_KEY  = 'relay'

// relay_handle is session-tier only and saved through its own endpoint (the backend keeps
// the handle independent of the whole-config PATCH). relay_channel_policies is edited by the
// policy editor: live (own endpoints) on a session, own-tier only in the form on profiles,
// templates and schedules. A session's Save never submits either.
const HANDLE_FIELD = {
  key: 'relay_handle',
  label: 'Handle',
  widget: 'text-input',
  monospace: true,
  placeholder: 'my-session',
  description: 'Used as !handle in Slack instructions. Lowercase letters, digits and dashes; unique across this Backend.',
}

const route = useRoute()
const settingsStore = useSettingsStore()
const templateStore = useTemplateStore()
const profileStore  = useProfileStore()
const sessionStore  = useSessionStore()
const scheduleStore = useScheduleStore()
const relayStore     = useRelayStore()

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
  return { ...profileBase.value, ...templateBase.value, ...cleanBase, ...cleanDraft }
})
const isDirty = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving  = ref(false)

// The policy field is rendered by the policy editor below, which needs the own tier alone.
const relayFields = FIELD_SCHEMAS.relay.filter(f => f.key !== 'relay_channel_policies')

const { fieldStates } = useEditSectionFieldStates({
  isTemplateMode, isSessionMode, isScheduleMode, baseConfig, draft,
  boundProfile, boundTemplate,
  schemaFields: FIELD_SCHEMAS.relay,
})
const { handleReset } = useEditSectionReset({ areaKey })

const { reasons: restartReasons, openRestart } = useRestartRequired(entityId)
const relayNotAttachedReason = computed(() =>
  isSessionMode.value ? restartReasons.value.find(r => r.startsWith('Relay attached')) || '' : ''
)

// ── Channel policies ──────────────────────────────────────────────────────
const soleRelayId = computed(() => (relayStore.relays.length === 1 ? relayStore.relays[0].relay_id : null))
const policyModel = ref(null)

// Live editing needs the relay to be the session's saved effective relay (the endpoints 409
// otherwise); the forms use the (possibly unsaved) selection.
const savedEffectiveRelayId = computed(() =>
  ({ ...profileBase.value, ...templateBase.value, ...baseConfig.value }).relay_id || null)
const policyEditorRelayId = computed(() => {
  if (!entity.value || isPermanentScheduleNA.value || isNotApplicable.value) return ''
  const id = isSessionMode.value ? savedEffectiveRelayId.value : mergedConfig.value.relay_id
  return id && relayStore.relays.some(r => r.relay_id === id) ? id : ''
})

watch(policyEditorRelayId, async (id) => {
  policyModel.value = null
  if (!id || isSessionMode.value) return
  try {
    policyModel.value = await relayStore.getPolicyModel(id)
  } catch {
    policyModel.value = null
  }
}, { immediate: true })

// Own tier only: the draft when edited, else this entity's own saved value (never the merged one).
const ownPolicies = computed(() => normalizePolicies(
  'relay_channel_policies' in (draft.value || {}) ? draft.value.relay_channel_policies : baseConfig.value.relay_channel_policies,
  soleRelayId.value,
))
const inheritedPolicies = computed(() => {
  const out = {}
  const id = policyEditorRelayId.value
  const tiers = [['profile', profileBase.value], ['template', templateBase.value]]
  for (const [source, cfg] of tiers) {
    const entries = normalizePolicies(cfg?.relay_channel_policies, soleRelayId.value)[id] || {}
    for (const [target, policy] of Object.entries(entries)) out[target] = { policy, source }
  }
  return out
})

// ── Server-side validation messages ───────────────────────────────────────
const saveError = ref('')
const fieldErrors = ref({})

function attributeError(message) {
  if (/footer/i.test(message)) return { relay_footer_template: message }
  if (/relay_id/i.test(message)) return { relay_id: message }
  return null
}

// ── Session handle ────────────────────────────────────────────────────────
const savedHandle = computed(() => entity.value?.config?.relay_handle || '')
const handleDraft = ref('')
const handleError = ref('')
const handleSaving = ref(false)
const handleDirty = computed(() => handleDraft.value !== savedHandle.value && handleDraft.value.trim() !== '')

watch(savedHandle, (h) => { handleDraft.value = h }, { immediate: true })

async function saveHandle() {
  handleSaving.value = true
  handleError.value = ''
  try {
    const { relay_handle: handle } = await relayStore.setSessionHandle(entityId.value, handleDraft.value.trim())
    sessionStore.updateSession(entityId.value, { config: { ...(entity.value?.config || {}), relay_handle: handle } })
    handleDraft.value = handle
  } catch (err) {
    handleError.value = err.message
  } finally {
    handleSaving.value = false
  }
}

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

async function handleSave() {
  if (!entity.value || !isDirty.value) return
  saving.value = true
  saveError.value = ''
  fieldErrors.value = {}
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
    const attributed = attributeError(err.message || '')
    if (attributed) fieldErrors.value = attributed
    else saveError.value = err.message || 'Save failed'
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  saveError.value = ''
  fieldErrors.value = {}
  settingsStore.discardDraft(areaKey.value)
}

defineExpose({ save: handleSave, cancel: handleCancel })

onMounted(() => {
  relayStore.fetchIfEmpty()
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

.save-error {
  color: #f87171;
  font-size: 12px;
  padding: 6px 10px;
  margin-bottom: 10px;
  border-radius: 6px;
  background: rgba(248, 113, 113, 0.08);
  border: 1px solid rgba(248, 113, 113, 0.25);
}

.restart-note { font-size: 12px; color: #d29922; margin: 8px 0 0; }
.handle-block { margin-top: 8px; }
.policy-block { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--bs-border-color); }
.handle-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 6px 0 0 232px; }
@container settings-area (max-width: 599px) {
  .handle-actions { padding-left: 0; }
}

.restart-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  background: rgba(210, 153, 34, 0.15);
  border: 1px solid rgba(210, 153, 34, 0.5);
  color: #d29922;
}

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
</style>
