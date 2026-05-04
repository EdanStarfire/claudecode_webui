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
          :class="{ 'profile-row--confirm': pendingDeleteId === profile.profile_id }"
          role="button"
          tabindex="0"
          @click="pendingDeleteId !== profile.profile_id && openEdit(profile)"
          @keydown.enter.prevent="pendingDeleteId !== profile.profile_id && openEdit(profile)"
          @keydown.space.prevent="pendingDeleteId !== profile.profile_id && openEdit(profile)"
        >
          <template v-if="pendingDeleteId === profile.profile_id">
            <span class="confirm-text">Delete "{{ profile.name }}"?</span>
            <div class="confirm-btns">
              <button
                class="btn-confirm-delete"
                :disabled="deleting"
                @click.stop="executeDelete(profile.profile_id)"
              >{{ deleting ? '…' : 'Delete' }}</button>
              <button class="btn-confirm-cancel" @click.stop="cancelDelete">Cancel</button>
            </div>
          </template>
          <template v-else>
            <span class="profile-name">{{ profile.name }}</span>
            <div class="row-right">
              <button
                class="row-delete-btn"
                title="Delete profile"
                @click.stop="pendingDeleteId = profile.profile_id"
              >✕</button>
              <span class="row-chevron" aria-hidden="true">›</span>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProfileStore } from '@/stores/profile'
import SettingsToolbar from '../SettingsToolbar.vue'

const route = useRoute()
const router = useRouter()
const profileStore = useProfileStore()
const pendingDeleteId = ref(null)
const deleting = ref(false)

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

function cancelDelete() {
  pendingDeleteId.value = null
}

async function executeDelete(profileId) {
  if (deleting.value) return
  deleting.value = true
  try {
    await profileStore.deleteProfile(profileId)
    if (route.params.profileId === profileId) {
      router.push('/settings/profiles')
    }
  } catch (err) {
    console.error('Delete profile failed:', err)
  } finally {
    deleting.value = false
    pendingDeleteId.value = null
  }
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

.profile-row:hover .row-delete-btn {
  opacity: 1;
}

.profile-row:focus-visible {
  outline: 2px solid #3fb950;
  outline-offset: 1px;
}

.profile-row--confirm {
  border-color: rgba(248, 113, 113, 0.5);
  background: rgba(248, 113, 113, 0.06);
  cursor: default;
}

.profile-row--confirm:hover {
  border-color: rgba(248, 113, 113, 0.7);
  background: rgba(248, 113, 113, 0.06);
}

.profile-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.row-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.row-chevron {
  font-size: 18px;
  color: var(--bs-tertiary-color);
  line-height: 1;
}

.row-delete-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: none;
  background: none;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s, background 0.12s, color 0.12s;
}

.row-delete-btn:hover {
  background: rgba(248, 113, 113, 0.15);
  color: #f87171;
}

/* ── Inline confirm ────────────────────────── */
.confirm-text {
  font-size: 12px;
  color: var(--bs-secondary-color);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.confirm-btns {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.btn-confirm-delete,
.btn-confirm-cancel {
  padding: 3px 10px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.btn-confirm-delete {
  background: rgba(248, 113, 113, 0.15);
  border-color: rgba(248, 113, 113, 0.4);
  color: #f87171;
}

.btn-confirm-delete:hover:not(:disabled) {
  background: #f87171;
  border-color: #f87171;
  color: #fff;
}

.btn-confirm-delete:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn-confirm-cancel {
  background: none;
  border-color: var(--bs-border-color);
  color: var(--bs-secondary-color);
}

.btn-confirm-cancel:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
