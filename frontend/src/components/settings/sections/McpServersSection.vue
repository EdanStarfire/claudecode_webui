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
        @update:mcp-server-ids="v => handleField('mcp_server_ids', v)"
        @update:claude-ai-enabled="v => handleField('enable_claudeai_mcp_servers', v)"
        @update:local-enabled="v => handleField('strict_mcp_config', !v)"
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
import McpServerPanel from '../../configuration/McpServerPanel.vue'

const PROFILE_AREA = 'mcp'
const SECTION_KEY  = 'mcp-servers'

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

const baseConfig = computed(() => {
  if (isTemplateMode.value) return entity.value || {}
  return entity.value?.config || {}
})

const draft        = computed(() => settingsStore.getDraft(areaKey.value))
const mergedConfig = computed(() => ({ ...baseConfig.value, ...draft.value }))
const isDirty      = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving       = ref(false)

const boundProfileId = computed(() => {
  if (!isTemplateMode.value) return null
  return entity.value?.profile_ids?.[PROFILE_AREA] || null
})
const boundProfile = computed(() => boundProfileId.value ? profileStore.getProfile(boundProfileId.value) : null)

const toolbarChips = computed(() => {
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
    const d = draft.value
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
