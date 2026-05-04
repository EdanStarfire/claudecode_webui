<template>
  <div class="lib-section">
    <SettingsToolbar title="Profiles" />

    <div v-if="profileStore.loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>
    <div v-else-if="profileStore.error" class="section-error">{{ profileStore.error }}</div>

    <div v-else class="section-body">
      <div
        v-for="area in PROFILE_AREAS"
        :key="area.key"
        class="category-block"
      >
        <div class="category-header">
          <span class="category-label">{{ area.label }}</span>
          <button
            class="category-add-btn"
            :title="`New ${area.label} profile`"
            @click="quickCreate(area.key)"
          >+</button>
        </div>

        <div v-if="profilesByArea(area.key).length === 0" class="empty-state">
          No {{ area.label.toLowerCase() }} profiles. Click + to create one.
        </div>

        <div
          v-for="profile in profilesByArea(area.key)"
          :key="profile.profile_id"
          class="profile-row"
          role="button"
          tabindex="0"
          @click="openEdit(profile)"
          @keydown.enter.prevent="openEdit(profile)"
          @keydown.space.prevent="openEdit(profile)"
        >
          <span class="profile-name">{{ profile.name }}</span>
          <span class="row-chevron" aria-hidden="true">›</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProfileStore } from '@/stores/profile'
import SettingsToolbar from '../SettingsToolbar.vue'

const router = useRouter()
const profileStore = useProfileStore()

const PROFILE_AREAS = [
  { key: 'model',         label: 'Model' },
  { key: 'permissions',   label: 'Permissions' },
  { key: 'system_prompt', label: 'System Prompt' },
  { key: 'mcp',           label: 'MCP Servers' },
  { key: 'isolation',     label: 'Isolation' },
  { key: 'features',      label: 'Features' },
]

function profilesByArea(areaKey) {
  return profileStore.allProfiles
    .filter(p => p.area === areaKey)
    .sort((a, b) => a.name.localeCompare(b.name))
}

function openEdit(profile) {
  router.push(`/settings/profile/${profile.profile_id}/general`)
}

function quickCreate(areaKey) {
  router.push(`/settings/profile/__new__/general?area=${areaKey}`)
}

onMounted(() => profileStore.fetchIfEmpty())
</script>

<style scoped>
.lib-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-status {
  padding: 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

.status-spinner {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

.section-error {
  padding: 16px 20px;
  color: #f87171;
  font-size: 13px;
  background: rgba(248, 113, 113, 0.08);
  border-bottom: 1px solid rgba(248, 113, 113, 0.2);
}

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Category ─────────────────────────────── */
.category-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.category-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 4px;
  border-bottom: 1px solid var(--bs-border-color);
}

.category-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-secondary-color);
}

.category-add-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.category-add-btn:hover:not(:disabled) {
  background: #3fb950;
  border-color: #3fb950;
  color: #fff;
}

.category-add-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ── Rows ─────────────────────────────────── */
.empty-state {
  padding: 14px 12px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border-radius: 7px;
  border: 1px dashed var(--bs-border-color);
}

.profile-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  border-radius: 7px;
  border: 1px solid var(--bs-border-color);
  cursor: pointer;
  background: var(--bs-body-bg);
  transition: background 0.12s, border-color 0.12s;
}

.profile-row:hover {
  background: var(--bs-tertiary-bg);
  border-color: #3fb950;
}

.profile-row:focus-visible {
  outline: 2px solid #3fb950;
  outline-offset: 1px;
}

.profile-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.row-chevron {
  font-size: 18px;
  color: var(--bs-tertiary-color);
  flex-shrink: 0;
  line-height: 1;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
