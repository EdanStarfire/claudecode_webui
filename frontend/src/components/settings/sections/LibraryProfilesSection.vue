<template>
  <div class="lib-section">
    <SettingsToolbar title="Profiles" />

    <div v-if="profileStore.loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>
    <div v-else-if="profileStore.error" class="section-error">{{ profileStore.error }}</div>

    <div v-else class="section-body">
      <div class="list-header">
        <span class="list-count">{{ profileStore.allProfiles.length }} profile{{ profileStore.allProfiles.length === 1 ? '' : 's' }}</span>
        <button class="btn-action btn-primary-action" @click="openCreate">+ New Profile</button>
      </div>

      <div v-if="profileStore.allProfiles.length === 0" class="empty-state">
        No profiles yet. Create a profile to define reusable configuration for a specific area.
      </div>

      <div
        v-for="profile in sortedProfiles"
        :key="profile.profile_id"
        class="profile-row"
        role="button"
        tabindex="0"
        @click="openEdit(profile)"
        @keydown.enter.prevent="openEdit(profile)"
        @keydown.space.prevent="openEdit(profile)"
      >
        <div class="profile-info">
          <span class="profile-name">{{ profile.name }}</span>
        </div>
        <div class="profile-meta">
          <span class="meta-badge area-badge" :class="`area-${profile.area}`">{{ AREA_LABELS[profile.area] || profile.area }}</span>
          <span class="row-chevron" aria-hidden="true">›</span>
        </div>
      </div>
    </div>

    <!-- Create modal -->
    <div v-if="creating" class="create-overlay" @click.self="creating = false">
      <div class="create-modal">
        <div class="create-header">New Profile</div>
        <div class="create-field">
          <label class="create-label">Name</label>
          <input
            ref="nameInputRef"
            v-model="newName"
            type="text"
            class="create-input"
            placeholder="e.g., Strict Permissions"
            @keydown.enter="submitCreate"
            @keydown.escape="creating = false"
          />
        </div>
        <div class="create-field">
          <label class="create-label">Area</label>
          <select v-model="newArea" class="create-select">
            <option v-for="(label, key) in AREA_LABELS" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <div v-if="createError" class="create-error">{{ createError }}</div>
        <div class="create-actions">
          <button class="btn-action btn-secondary-action" @click="creating = false">Cancel</button>
          <button class="btn-action btn-primary-action" :disabled="!newName.trim() || saving" @click="submitCreate">
            {{ saving ? 'Creating…' : 'Create' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProfileStore } from '@/stores/profile'
import SettingsToolbar from '../SettingsToolbar.vue'

const router = useRouter()
const profileStore = useProfileStore()

const AREA_LABELS = {
  model:         'Model',
  permissions:   'Permissions',
  system_prompt: 'System Prompt',
  mcp:           'MCP',
  isolation:     'Isolation',
  features:      'Features',
}

const AREA_ORDER = Object.keys(AREA_LABELS)

const sortedProfiles = computed(() =>
  [...profileStore.allProfiles].sort((a, b) => {
    const ai = AREA_ORDER.indexOf(a.area)
    const bi = AREA_ORDER.indexOf(b.area)
    if (ai !== bi) return ai - bi
    return a.name.localeCompare(b.name)
  })
)

function openEdit(profile) {
  router.push(`/settings/profile/${profile.profile_id}/general`)
}

// ── Create ────────────────────────────────────────────────────────────
const creating = ref(false)
const newName = ref('')
const newArea = ref('model')
const saving = ref(false)
const createError = ref('')
const nameInputRef = ref(null)

async function openCreate() {
  newName.value = ''
  newArea.value = 'model'
  createError.value = ''
  creating.value = true
  await nextTick()
  nameInputRef.value?.focus()
}

async function submitCreate() {
  if (!newName.value.trim() || saving.value) return
  saving.value = true
  createError.value = ''
  try {
    const profile = await profileStore.createProfile(newName.value.trim(), newArea.value, {})
    creating.value = false
    router.push(`/settings/profile/${profile.profile_id}/general`)
  } catch (err) {
    createError.value = err.message || 'Failed to create profile'
  } finally {
    saving.value = false
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
  position: relative;
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
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.list-count {
  font-size: 13px;
  color: var(--bs-secondary-color);
}

.btn-action {
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.btn-secondary-action {
  background: none;
  border-color: var(--bs-border-color);
  color: var(--bs-secondary-color);
}

.btn-secondary-action:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

.btn-primary-action {
  background: #6366f1;
  border-color: #6366f1;
  color: #fff;
}

.btn-primary-action:hover:not(:disabled) {
  background: #818cf8;
  border-color: #818cf8;
}

.btn-primary-action:disabled {
  opacity: 0.5;
  cursor: default;
}

.empty-state {
  padding: 32px 16px;
  text-align: center;
  color: var(--bs-secondary-color);
  font-size: 13px;
  background: var(--bs-tertiary-bg);
  border-radius: 8px;
  border: 1px dashed var(--bs-border-color);
}

.profile-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid var(--bs-border-color);
  margin-bottom: 8px;
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

.profile-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.profile-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.profile-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.meta-badge {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 10px;
  white-space: nowrap;
}

.area-badge {
  background: var(--p-tint, rgba(63, 185, 80, 0.12));
  color: var(--p-fg, #3fb950);
  border: 1px solid var(--p-border, rgba(63, 185, 80, 0.30));
}

.row-chevron {
  font-size: 18px;
  color: var(--bs-tertiary-color);
  line-height: 1;
}

/* ── Create modal ──────────────────────────────────────────────────── */
.create-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}

.create-modal {
  background: var(--bs-body-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 10px;
  padding: 20px;
  width: 340px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.create-header {
  font-size: 15px;
  font-weight: 600;
  color: var(--bs-emphasis-color);
}

.create-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.create-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.create-input,
.create-select {
  background: var(--bs-body-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 13px;
  color: var(--bs-body-color);
  width: 100%;
}

.create-input:focus,
.create-select:focus {
  outline: none;
  border-color: #3fb950;
}

.create-error {
  font-size: 12px;
  color: #f87171;
}

.create-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
